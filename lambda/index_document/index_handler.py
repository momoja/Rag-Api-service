import json
import logging
import os
from urllib.parse import unquote_plus

import psycopg

from rag_agent.embed import EMBEDDED_PREFIX
from rag_agent.observability import bind, bind_invocation, configure_logging
from rag_agent.vector import index_document

logger = logging.getLogger(__name__)

_EMBEDDINGS_SUFFIX = "/embeddings.jsonl"

_s3_client = None
_conn = None


def _is_embeddings_key(key: str) -> bool:
    return key.startswith(EMBEDDED_PREFIX) and key.endswith(_EMBEDDINGS_SUFFIX)


def _get_s3():  
    global _s3_client
    if _s3_client is None:
        import boto3

        _s3_client = boto3.client("s3")
    return _s3_client


def _connect():
    dsn = os.environ.get("DB_DSN")
    if dsn:
        return psycopg.connect(dsn, connect_timeout=10)
    secret_arn = os.environ.get("SECRET_ARN")
    if secret_arn:
        import boto3

        secret = json.loads(
            boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
        )
        return psycopg.connect(
            host=secret["host"],
            port=int(secret.get("port", 5432)),
            dbname=secret["dbname"],
            user=secret["username"],
            password=secret["password"],
            connect_timeout=10,
        )
    raise RuntimeError("index-document requires DB_DSN or SECRET_ARN")


def _get_conn():
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn


def _key_of(record: dict) -> str:
    try:
        return unquote_plus(record["s3"]["object"]["key"])
    except (KeyError, TypeError):
        return "?"


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
    if not _is_embeddings_key(key):
        raise ValueError("record key is not an embedded embeddings file")
    bind(key=key)

    result = index_document(bucket, key, s3_client=_get_s3(), conn=_get_conn())
    bind(document_id=result["document_id"], num_embeddings=result["num_embeddings"])
    return {
        "key": key,
        "document_id": result["document_id"],
        "num_embeddings": result["num_embeddings"],
    }


def lambda_handler(event: dict, context) -> dict:
    configure_logging()
    bind_invocation(context, stage="index")
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
            # Transient (DB down, S3 hiccup, ...): let S3 retry the batch.
            logger.exception("failed to process %s", _key_of(record))
            failures.append({"key": _key_of(record)})

    logger.info(
        "index invocation complete: processed=%d skipped=%d failed=%d",
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
