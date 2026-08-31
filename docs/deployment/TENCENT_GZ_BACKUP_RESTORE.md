# Backup and Restore

`backup.sh` creates an atomic timestamp directory under
`/srv/medtrust/backups` containing:

- PostgreSQL custom-format logical dump;
- migration version and table-count report;
- consistent MinIO named-volume archive taken while write services are
  stopped;
- redacted non-secret environment configuration;
- Git/package version and Compose version;
- SHA-256 manifest and completion marker.

The script restarts stopped services on failure and never marks an incomplete
backup successful.

`restore-test.sh <backup-directory>`:

- verifies all hashes;
- restores PostgreSQL into an independent temporary container and volume;
- checks migration version and critical tables;
- restores MinIO files into an independent temporary volume;
- reports object-file count;
- removes only temporary containers and volumes;
- never modifies production or deletes the source backup.

Same-server backup is insufficient disaster recovery. Add encrypted off-host
or Tencent COS copies with a separate access policy after this Alpha, and test
restoration from that copy.
