from __future__ import annotations

import argparse

from minio import Minio


ALLOWED_BUCKETS = {
    "medtrust-phase4-approved-results",
    "medtrust-phase56-quarantined-results",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear one dedicated MedTrust demo object bucket")
    parser.add_argument("--endpoint", default="127.0.0.1:9000")
    parser.add_argument("--access-key", default="medtrust")
    parser.add_argument("--secret-key", default="medtrust_dev_only")
    parser.add_argument("--bucket", default="medtrust-phase4-approved-results")
    args = parser.parse_args()
    if args.bucket not in ALLOWED_BUCKETS:
        raise SystemExit("Refusing to clear an unexpected bucket")
    client = Minio(args.endpoint, access_key=args.access_key, secret_key=args.secret_key, secure=False)
    if not client.bucket_exists(args.bucket):
        return
    for item in client.list_objects(args.bucket, recursive=True):
        client.remove_object(args.bucket, item.object_name)


if __name__ == "__main__":
    main()
