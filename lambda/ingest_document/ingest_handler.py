"""ingest-document Lambda — processes S3 object-created events for uploads/.

Chapter 6: when a document lands in <bucket>/uploads/ (minted by the Chapter
5 presign API), S3's bucket notification invokes this function with a direct
S3 event (NOT the API Gateway v2 shape the presign handler sees):

    event = {
        "Records": [{
            "eventName": "ObjectCreated:Put",
            "s3": {
                "bucket": {"name": "rag-agent-dev-documents-..."},
                "object": {
                    "key": "uploads/report.pdf",   # URL-encoded in transit
                    "size": 12345,
                    "versionId": "VERSION_ID",     # bucket is versioned
                },
            },
        }]
    }

All business logic lives in rag_agent.ingest; this module is thin glue:
parse records -> call core -> classify outcomes. Permanent content problems
(ValueError: unsupported/corrupt document) are skipped so S3 does not retry
them forever; transient failures re-raise so the notification retries the
whole batch — re-processing is idempotent (deterministic document_id per
object version overwrites the same processed/ keys).
"""

import logging
from urllib.parse import unquote_plus

from rag_agent.ingest import process_document
from rag_agent.observability import bind, bind_invocation, configure_logging

logger = logging.getLogger(__name__)


def _records_of(event: dict) -> list[dict]:
    records = event.get("Records")
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def _process_record(record: dict, *, chunk_size: int, overlap: int) -> dict:
    s3 = record.get("s3")
    if not isinstance(s3, dict):
        raise ValueError("record has no 's3' section")
    bucket_info = s3.get("bucket")
    object_info = s3.get("object")
    if not isinstance(bucket_info, dict) or not isinstance(object_info, dict):
        raise ValueError("record 's3' section is missing bucket/object")
    bucket = bucket_info.get("name")
    key = unquote_plus(object_info.get("key") or "")
    version_id = object_info.get("versionId") or None
    if not isinstance(bucket, str) or not bucket:
        raise ValueError("record has no bucket name")
    bind(key=key, version_id=version_id)

    result = process_document(
        bucket,
        key,
        version_id,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    bind(document_id=result["document_id"], num_chunks=result["num_chunks"])
    return {
        "key": key,
        "version_id": version_id,
        "document_id": result["document_id"],
        "num_chunks": result["num_chunks"],
    }


def lambda_handler(event: dict, context) -> dict:
    configure_logging()
    bind_invocation(context, stage="ingest")
    processed: list[dict] = []
    skipped: list[dict] = []
    failures: list[dict] = []
    for record in _records_of(event):
        # The bucket notification only emits s3:ObjectCreated:*; skipping
        # anything else is defensive, not a supported path.
        if not str(record.get("eventName", "")).startswith("ObjectCreated:"):
            continue
        try:
            processed.append(_process_record(record, chunk_size=1200, overlap=200))
        except ValueError as exc:
            # Permanent content problem: log and skip, never retry forever.
            logger.warning("skipping %s: %s", _describe(record), exc)
            skipped.append({"key": _key_of(record), "reason": str(exc)})
        except Exception:
            # Transient (network, throttling, ...): let S3 retry the batch.
            logger.exception("failed to process %s", _describe(record))
            failures.append({"key": _key_of(record)})

    logger.info(
        "ingest invocation complete: processed=%d skipped=%d failed=%d",
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


def _key_of(record: dict) -> str:
    try:
        return unquote_plus(record["s3"]["object"]["key"])
    except (KeyError, TypeError):
        return "?"


def _describe(record: dict) -> str:
    s3 = record.get("s3")
    try:
        bucket = s3["bucket"]["name"]
        key = unquote_plus(s3["object"]["key"])
        return f"s3://{bucket}/{key}"
    except (KeyError, TypeError):
        return "unknown record"
