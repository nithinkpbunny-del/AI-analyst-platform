# Runbook: backup and restore (PostgreSQL and object storage)

For whoever runs the platform: what is backed up, how to restore after a mistake or a lost database, and how to
prove the backups work. Production is AWS `ap-south-1` (docs/DEPLOY_AWS.md); everything here was tested locally with
`make backup-drill`.

## What holds data

| Store | Holds | Loss would mean |
|---|---|---|
| PostgreSQL (RDS) | workspaces, people, sessions, connections (credentials encrypted), dictionary, chats and answers, reports, goals, tiles, usage and billing events, the activity log | everything; the core backup |
| Object storage (S3 bucket) | uploaded files (the customer's source for file connections), cached snapshots (expire within a day), report PDFs and Excel files | uploaded files and reports; snapshots rebuild themselves |
| Redis (ElastiCache) | rate-limit counters, locks, short reuse caches, the Celery queue | nothing lasting: queued jobs are retried by people, counters restart. **Not backed up.** |
| The encryption key (KMS key, or `MASTER_KEY`) | wraps every stored credential, AI key and 2FA secret | every connection must be re-authorised and every 2FA set up again. **Backed up separately, see below.** |

## Targets

- **RPO** (data you can lose): 5 minutes for the database (RDS point-in-time recovery), 1 day for objects outside
  versioning (versioning makes overwrites and deletes recoverable at once).
- **RTO** (time to be back): about 1 hour for a database restore of a pilot-sized database; the drill shows the
  steps take minutes, the rest is checking.
- Retention: database backups 14 days (PITR) plus a monthly snapshot kept 12 months; object versions 30 days.

## 1. Production backups (automatic, set up once)

**PostgreSQL (RDS):**
1. Automated backups on, retention 14 days (this gives point-in-time recovery to any second in that window).
2. A monthly manual snapshot (AWS Backup plan "monthly, keep 12 months"), copied to a second region inside India
   (`ap-south-2`, Hyderabad) for disaster recovery. Snapshots are encrypted with the RDS KMS key.
3. Deletion protection on the instance.

**S3 bucket:**
1. Versioning on, and a lifecycle rule that keeps non-current versions 30 days and deletes them after.
2. Same-region replication or AWS Backup for S3 to a backup bucket in another account (so one compromised account
   cannot delete both), SSE-KMS on both.
3. The `orgs/*/snapshots/` prefix may be excluded from replication (cached copies, rebuilt on the next question).

**The key:**
- With KMS (production): the key is never exported. Keep it safe from deletion: a key policy that denies
  `kms:ScheduleKeyDeletion` to everyone but a break-glass role, and AWS's 30-day deletion waiting period.
- With `MASTER_KEY` (any installation not on KMS): store the value in AWS Secrets Manager **and** in an offline
  password manager entry kept by two named people. A database backup without it cannot open any credential.

**Portable backups** (a copy outside AWS, before a risky migration, or for a customer's own archive):

```
cd backend
DATABASE_URL=... S3_ENDPOINT=... S3_BUCKET=... S3_ACCESS_KEY=... S3_SECRET_KEY=... \
  uv run python -m ops.backup backup --out /secure/backups
```

This writes `/secure/backups/<UTC stamp>/` with `postgres.dump`, `objects/` and `manifest.json` (row counts,
policies, triggers, object checksums, migration revision). The folder is created readable by its owner only. It
holds customer data: keep it on encrypted storage and delete it on the retention schedule above.

## 2. Restore the database to a point in time (RDS)

Use for: rows deleted or changed by mistake, a bad migration, a damaged database.

1. **Stop writes**: scale the `api`, `worker` and `beat` services to 0 (ECS: desired count 0). The web app shows its
   "cannot reach the server" message.
2. RDS console > the instance > *Restore to point in time*, a time just before the problem, a **new** instance name
   (`analyst-db-restore-YYYYMMDD`), same subnet group, security group and parameter group. Never restore over the
   running instance.
3. When it is available, verify it before switching (section 4) with its endpoint.
4. Switch: update the `DATABASE_URL` secret in Secrets Manager to the new endpoint (or rename the instances so the
   old endpoint points to the new one), then scale `api`, `worker` and `beat` back up. Migrations run at start-up
   and do nothing if the revision matches.
5. Keep the old instance stopped for 7 days, then delete it with a final snapshot.

## 3. Restore from a portable backup (any PostgreSQL 16 and S3-compatible storage)

```
# 1. An EMPTY database (the tool refuses one that has tables):
createdb -h <host> -U <admin> analyst_restored

# 2. Database (creates the app_tenant role first if the server is new), then the objects:
cd backend
S3_ENDPOINT=... S3_BUCKET=<empty or new bucket> S3_ACCESS_KEY=... S3_SECRET_KEY=... \
  uv run python -m ops.backup restore --from /secure/backups/<stamp> \
    --database-url postgresql://<admin>:<password>@<host>:5432/analyst_restored --objects

# 3. Check it (section 4), then point DATABASE_URL and S3_BUCKET at it.
uv run python -m ops.backup verify --from /secure/backups/<stamp> \
    --database-url postgresql://<admin>:<password>@<host>:5432/analyst_restored --objects
```

The restoring user must be able to create the `citext` extension and grant roles (the RDS master user can). The
PostgreSQL client tools must be version 16: the tool uses a local `pg_dump`/`pg_restore` 16 if there is one,
otherwise Docker's `postgres:16-alpine` image, with the password passed in the environment.

Restore single objects instead (a file someone removed by mistake within 30 days): S3 console > the object > *Show
versions* > download or restore the version before the delete marker.

## 4. Verify a restore before anyone uses it

`ops.backup verify` checks, against the backup's manifest:
- the migration revision is the same;
- **every table has the same number of rows**;
- every RLS policy and trigger exists (`tenant_isolation` on every tenant table, the activity log's append-only
  triggers);
- **tenant isolation still works**: as `app_tenant` with one workspace's id, no row of another workspace is visible
  and none can be changed, and the activity log still refuses UPDATE and DELETE;
- with `--objects`, every object is present with the same SHA-256.

For an RDS point-in-time restore there is no manifest: run `uv run python -m ops.backup backup --out /tmp/check
--no-objects` against the **old** instance first if it still answers, then `verify` the new one; otherwise check by
hand: `SELECT version_num FROM alembic_version`, row counts of `organizations`, `memberships`, `messages`,
`audit_log` against yesterday's metrics, and sign in to two test workspaces.

Then a manual smoke test: sign in, open Today, open a connection, ask a saved question (fake-model environments) or
open an earlier answer (production).

## 5. The restore drill (prove it works)

```
make backup-drill
```

Throwaway PostgreSQL 16 and S3-compatible storage containers; two workspaces with people, activity and files;
backup; a scratch database created empty and the bucket wiped; restore; a second restore into the now-filled
database must be refused; verify everything in section 4. It touches nothing outside its containers and prints
"Restore drill passed" (about 5 seconds after the images are downloaded). Run it after every migration that adds a
table, policy or trigger, and before every release. In production, repeat the RDS steps of section 2 against a
staging copy every quarter and record the time it took.

## 6. Special cases

- **A deleted workspace** (7-day grace passed): it was deleted on purpose (DPDP). Do not restore it into production.
  If an Owner proves it was a mistake, restore a point-in-time copy to a separate instance and give them a data
  export from there (Settings > Privacy > Export data), then delete that instance.
- **A person asks for their account to be erased** (no self-service yet, SECURITY.md 11.7): the operator first makes
  sure they are not a workspace's only Owner, removes them from every workspace (Team > Remove), then deletes their
  `users` row as the database owner. Every table that points at a person either goes with it (their sessions, 2FA,
  devices, consents, conversations, saved questions, tiles, briefing settings) or keeps the row without the person
  (goals, reports and connections they created). Activity-log entries keep the person's id until their workspaces
  are deleted (then they are anonymised, D124): they are the workspace's security record. Note the request in the
  operations log; backups age out within the retention period.
- **The key is lost** (`MASTER_KEY` gone, or the KMS key deleted): restore cannot help. Every connection shows "Sign in
  again"; Owners re-authorise each source, re-enter AI keys and set up 2FA again. This is why section 1 keeps the key
  separately.
