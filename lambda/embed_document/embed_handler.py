"""embed-document Lambda — embeds Chapter 6's staged chunks for Chapter 8.

Triggered by the documents bucket notification on ``processed/*/chunks.jsonl``
(suffix-filtered to ``.jsonl``, so metadata.json never fires it) — a direct
S3 event, same record shape as the ingest-document handler documents:

    event = {
        "Records": [{
            "eventName": "ObjectCreated:Put",
            "s3": {
                "bucket": {"name": "rag-agent-dev-documents-..."},
                "object": {"key": "processed/<document_id>/chunks.jsonl"},
            },
        }]
    }

All business logic lives in rag_agent.embed; this module is thin glue:
parse records -> call core -> classify outcomes. Permanent content problems
(ValueError: bad path, malformed chunk records, model input rejection) are
skipped so S3 does not retry them forever; transient Bedrock failures
exhaust their per-text retries (rag_agent.embed.embed_texts) then re-raise
so S3 retries the whole document — idempotent, because
``embedded/<document_id>/`` keys derive from the source path and PutObject
overwrites. Records for keys outside ``processed/<id>/chunks.jsonl`` cannot
arrive through the notification filters and are ignored defensively.
"""

import logging
from urllib.parse import unquote_plus

from rag_agent.embed import CHUNKS_SUFFIX, embed_document
from rag_agent.ingest import PROCESSED_PREFIX
from rag_agent.observability import bind, bind_invocation, configure_logging

logger = logging.getLogger(__name__)


def _key_of(record: dict) -> str:
    try:
        return unquote_plus(record["s3"]["object"]["key"])
    except (KeyError, TypeError):
        return "?"


def _is_chunks_key(key: str) -> bool:
    return key.startswith(PROCESSED_PREFIX) and key.endswith(CHUNKS_SUFFIX)


def _process_record(record: dict) -> dict:
    s3 = record.get("s3")
    bucket_info = s3.get("bucket") if isinstance(s3, dict) else None
    object_info = s3.get("object") if isinstance(s3, dict) else None
    if not isinstance(bucket_info, dict) or not isinstance(object_info, dict):
        raise ValueError("record 's3' section is missing bucket/object")
    bucket = bucket_info.get("name")
    key = unquote_plus(object_info.get("key") or "")
    if not isinstance(bucket, str) or not bucket:
        raise ValueError("record has no bucket name")
    if not _is_chunks_key(key):
        raise ValueError("record key is not a processed chunks file")
    bind(key=key)

    result = embed_document(bucket, key)
    bind(document_id=result["document_id"], num_embeddings=result["num_embeddings"])
    return {
        "key": key,
        "document_id": result["document_id"],
        "num_embeddings": result["num_embeddings"],
    }


def lambda_handler(event: dict, context) -> dict:
    configure_logging()
    bind_invocation(context, stage="embed")
    processed: list[dict] = []
    skipped: list[dict] = []
    failures: list[dict] = []
    for record in event.get("Records") if isinstance(event.get("Records"), list) else []:
        if not isinstance(record, dict):
            continue
        if not str(record.get("eventName", "")).startswith("ObjectCreated:"):
            continue
        try:
            processed.append(_process_record(record))
        except ValueError as exc:
            # Permanent content problem: log and skip, never retry forever.
            logger.warning("skipping %s: %s", _key_of(record), exc)
            skipped.append({"key": _key_of(record), "reason": str(exc)})
        except Exception:
            # Transient (Bedrock throttling past retries, network, ...):
            # let S3 retry the batch.
            logger.exception("failed to process %s", _key_of(record))
            failures.append({"key": _key_of(record)})

    logger.info(
        "embed invocation complete: processed=%d skipped=%d failed=%d",
        len(processed),
        len(skipped),
        len(failures),
    )
    if failures:
        raise RuntimeError(f"{len(failures)} record(s) failed transiently; retrying batch")
    return {
        "processed": processed,
        "skipped": skipped,
        "failed": failures,
    }
