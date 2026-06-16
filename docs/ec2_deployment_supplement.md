# EC2 Demo Deployment Notes

This document records the practical deployment workflow used for the EC2 demo environment.

It complements the main [README](../README.md), which already covers local setup, Docker services, seeding, and the normal application run flow. This note focuses on the demo-specific operational gaps:

- pre-populated data instead of full rebuilds
- `uvicorn` managed with `systemd`
- manual code sync with `rsync` instead of `git pull`

## Scope

These notes describe the demo environment only.

- They do not cover domain routing, TLS, or reverse proxy ownership.
- They assume the application code is placed on the EC2 instance at `/home/ubuntu/Youtube-Academy`.
- They assume FastAPI is run from a Python virtual environment on the instance.

## Baseline Setup

The baseline application setup is still based on the main README:

1. clone or copy the repository onto the EC2 host
2. create `.env` from `.env.example`
3. start the required Docker services
4. prepare the Python virtual environment
5. run FastAPI via `uvicorn`

For the original baseline commands, refer to:

- [README.md](../README.md)
- [youtube_embedding_pipeline.md](./youtube_embedding_pipeline.md)

## Demo Data Bootstrap

For the demo environment, some data-heavy steps were not rebuilt from scratch on EC2.

Instead, snapshots or restored copies of existing data volumes from another working development environment were used to populate:

- MySQL
- MongoDB
- Qdrant

This workaround was used because parts of the setup were too computationally expensive or too time-consuming for a quick demo rebuild, especially the seeding and embedding-related stages.

### Snapshot Payloads

The EC2 demo was populated by copying prepared data snapshots from a working development environment to the server and restoring them there. This did not require Qdrant API keys or public API access.

The remembered collection-level payloads were:

- `videos.archive.gz` - MongoDB archive for the `yta.videos` collection
- `quiz_generation.archive.gz` - MongoDB archive for the `yta.Quiz_Generation` collection
- `youtube_videos.snapshot` - Qdrant snapshot for the `youtube_videos__bge_base__768` collection

The repo root also contains `sf_skill_level_docs__bge_base__768.snapshot`, a Qdrant snapshot for the SkillsFuture skill-level collection. Restore or rebuild that collection separately if the server does not already have it.

MySQL SkillsFuture data still needs to exist through the normal seeding flow or a separate restore. The commands below cover the remembered YouTube video and quiz snapshot flow.

Do not commit snapshot payloads to git. They can contain generated data and can become large. Share them out-of-band with `rsync`, `scp`, S3, or another private channel.

### Create Collection Snapshot Files

Run these commands from a working source environment after the dataset has been validated.

Export the `videos` collection from MongoDB:

```bash
cd ~/Youtube-Academy

docker exec yta-mongodb sh -lc 'mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db yta --collection videos --gzip --archive=/tmp/videos.archive.gz'
docker cp yta-mongodb:/tmp/videos.archive.gz ./videos.archive.gz
```

Create and inspect the Qdrant snapshot for the YouTube embedding collection:

```bash
curl -X POST "http://127.0.0.1:6333/collections/youtube_videos__bge_base__768/snapshots"
curl "http://127.0.0.1:6333/collections/youtube_videos__bge_base__768/snapshots"
```

Download the Qdrant snapshot file. Replace `SNAPSHOT_NAME` with the returned name from the previous command.

```bash
curl -L "http://127.0.0.1:6333/collections/youtube_videos__bge_base__768/snapshots/SNAPSHOT_NAME" -o youtube_videos.snapshot
```

Check the generated files:

```bash
ls -lh videos.archive.gz youtube_videos.snapshot
```

Export the generated quiz questions collection:

```bash
cd ~/Youtube-Academy

docker exec yta-mongodb sh -lc 'mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db yta --collection Quiz_Generation --gzip --archive=/tmp/quiz_generation.archive.gz'
docker cp yta-mongodb:/tmp/quiz_generation.archive.gz ./quiz_generation.archive.gz
```

Check the quiz archive:

```bash
ls -lh quiz_generation.archive.gz
```

### Share Snapshot Files

Example using `rsync`:

```bash
rsync -avz --progress \
  videos.archive.gz \
  quiz_generation.archive.gz \
  youtube_videos.snapshot \
  ubuntu@<ec2-host>:~/Youtube-Academy/
```

If restoring the SkillsFuture Qdrant collection from a snapshot rather than rebuilding it, transfer that file too:

```bash
rsync -avz --progress sf_skill_level_docs__bge_base__768.snapshot ubuntu@<ec2-host>:~/Youtube-Academy/
```

### Restore Collection Snapshots On EC2

The exact historical restore command was not recorded. The commands below are a reproducible way to restore the same file types.

These commands assume:

- the MongoDB container is named `yta-mongodb`
- the Mongo image includes `mongorestore`, matching the export environment that used `mongodump`
- Mongo archives were created with the original `yta.<collection>` namespaces
- Qdrant is reachable on `127.0.0.1:6333`
- MySQL SkillsFuture data has already been seeded or restored separately

Start backing services:

```bash
cd ~/Youtube-Academy
docker compose up -d mysql mongodb qdrant
```

Restore the MongoDB archives:

```bash
docker cp ./videos.archive.gz yta-mongodb:/tmp/videos.archive.gz
docker exec yta-mongodb sh -lc 'mongorestore --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --drop --gzip --archive=/tmp/videos.archive.gz'

docker cp ./quiz_generation.archive.gz yta-mongodb:/tmp/quiz_generation.archive.gz
docker exec yta-mongodb sh -lc 'mongorestore --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --drop --gzip --archive=/tmp/quiz_generation.archive.gz'
```

Restore the Qdrant YouTube snapshot:

```bash
curl -X DELETE "http://127.0.0.1:6333/collections/youtube_videos__bge_base__768?wait=true"
curl -X POST \
  "http://127.0.0.1:6333/collections/youtube_videos__bge_base__768/snapshots/upload?priority=snapshot&wait=true" \
  -F "snapshot=@youtube_videos.snapshot"
```

If needed, restore the SkillsFuture Qdrant snapshot similarly:

```bash
curl -X DELETE "http://127.0.0.1:6333/collections/sf_skill_level_docs__bge_base__768?wait=true"
curl -X POST \
  "http://127.0.0.1:6333/collections/sf_skill_level_docs__bge_base__768/snapshots/upload?priority=snapshot&wait=true" \
  -F "snapshot=@sf_skill_level_docs__bge_base__768.snapshot"
```

Start or restart application services:

```bash
docker compose up -d --build --remove-orphans admin_tools
sudo systemctl restart <fastapi-service-name>
```

### Snapshot Restore Checks

Run quick checks before demo use:

```bash
docker compose exec -T mysql sh -c \
  'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "SHOW TABLES;"'

docker compose exec -T mongodb sh -c \
  'mongosh --username "$MONGO_INITDB_ROOT_USERNAME" \
    --password "$MONGO_INITDB_ROOT_PASSWORD" \
    --authenticationDatabase admin \
    "$MONGO_INITDB_DATABASE" \
    --quiet --eval "printjson({videos: db.videos.countDocuments(), quiz_generation: db.Quiz_Generation.countDocuments()})"'

curl -sS "http://127.0.0.1:${QDRANT_PORT:-6333}/collections"
```

Then verify the user-facing flows:

- `GET /api/public/sectors` returns sectors
- `GET /academy` loads the learner portal
- `http://<host>:5001/mongo_browser` can browse MongoDB records
- recommendations return non-empty results for a known restored skill/video combination

### Operational Implication

When restoring pre-populated data volumes:

- the server can start from a known-good dataset faster
- application code and stored data must remain compatible
- some code changes may still require partial reseeding, manual schema adjustment, or re-embedding

If the restored data is stale or incompatible with the current code, fall back to the README-based rebuild steps where feasible.

## Python Environment

The FastAPI application is expected to run from a local virtual environment on the EC2 host.

Example:

```bash
cd ~/Youtube-Academy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/vector_search.txt
```

If the SkillsFuture seeding script is run directly on EC2, install the extra seeder dependencies as well:

```bash
cd ~/Youtube-Academy
source .venv/bin/activate
pip install -r requirements/skillsfuture.txt
```

This was necessary because the direct Python seeder depends on packages such as `pandas` and `openpyxl`, which are not guaranteed to be present in the API runtime environment.

## FastAPI Service

For the demo environment, FastAPI was managed as a Linux service using `systemd`.

The service runs `uvicorn`, but the checked deployment did **not** use `--reload` in the service definition.

### Example Service Definition

Example unit file:

```ini
[Unit]
Description=Youtube Academy FastAPI
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/Youtube-Academy
Environment="PATH=/home/ubuntu/Youtube-Academy/.venv/bin"
ExecStart=/home/ubuntu/Youtube-Academy/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

### Useful Service Commands

Find likely service names:

```bash
systemctl list-units --type=service --all | grep -Ei 'uvicorn|fastapi|youtube|academy|api'
sudo grep -RniE 'uvicorn|api\.main:app|Youtube-Academy|FastAPI' /etc/systemd/system /lib/systemd/system
```

Inspect the service:

```bash
systemctl cat <service-name>
systemctl status <service-name> --no-pager -l
```

Reload and restart after config or backend code changes:

```bash
sudo systemctl daemon-reload
sudo systemctl restart <service-name>
systemctl status <service-name> --no-pager -l
journalctl -u <service-name> -n 100 --no-pager
```

## Why Rsync Was Used

The EC2 demo server was not updated using `git pull` from GitHub.

Reason:

- repository ownership and access constraints meant a suitable GitHub access token could not be generated for the server workflow

As a result, the practical deployment method used for the demo was manual file synchronization from a local machine using `rsync`.

## Rsync Deployment Workflow

The commands below assume:

- local repo path: current working directory is the repository root
- remote repo path: `~/Youtube-Academy`
- remote user: `ubuntu`

Always start with a dry run by adding `-n`.

### Frontend-Only Changes

For static frontend changes under `api/frontend/academy/static/`, sync only the changed files directly into the remote static directory.

Example dry run:

```bash
rsync -avzn --progress \
  ./api/frontend/academy/static/app.js \
  ./api/frontend/academy/static/job_roles_app.js \
  ./api/frontend/academy/static/styles.css \
  ubuntu@<ec2-host>:~/Youtube-Academy/api/frontend/academy/static/
```

Actual sync:

```bash
rsync -avz --progress \
  ./api/frontend/academy/static/app.js \
  ./api/frontend/academy/static/job_roles_app.js \
  ./api/frontend/academy/static/styles.css \
  ubuntu@<ec2-host>:~/Youtube-Academy/api/frontend/academy/static/
```

Notes:

- a FastAPI restart is usually not required for static file replacement
- a hard refresh in the browser may still be needed

### Backend Python Changes

For Python files, preserve the repo-relative paths on the server with `--relative`.

Example dry run:

```bash
rsync -avzn --progress --relative \
  ./api/main.py \
  ./api/routes/learner_portal.py \
  ./api/routes/job_role_lookup_utils.py \
  ubuntu@<ec2-host>:~/Youtube-Academy/
```

Actual sync:

```bash
rsync -avz --progress --relative \
  ./api/main.py \
  ./api/routes/learner_portal.py \
  ./api/routes/job_role_lookup_utils.py \
  ubuntu@<ec2-host>:~/Youtube-Academy/
```

After syncing backend Python changes, restart the FastAPI service:

```bash
sudo systemctl restart <service-name>
```

### Mixed Frontend and Backend Changes

If a change touches both static assets and backend code:

1. sync the changed files
2. restart the FastAPI service if any Python code changed
3. hard refresh the browser to avoid stale static assets

## Example Update Procedure

This is the typical EC2 demo update flow:

1. identify the files changed in the local repo
2. choose the correct `rsync` command based on whether the change is frontend-only or includes backend files
3. dry run with `-n`
4. run the actual sync
5. restart the FastAPI `systemd` service if backend files changed
6. verify the relevant page or API endpoint


## Known Constraints

- demo data may come from restored snapshots instead of a fresh full pipeline run
- GitHub-based pull deployment was not used because repository access on the server was constrained
- `rsync` was used as the practical deployment method
- FastAPI was managed as a `systemd` service using `uvicorn`
- the checked service configuration did not include `--reload`

## When to Fall Back to README Steps

Use the full README flow instead of the shortcut demo workflow when:

- the restored data volumes are missing or corrupted
- schema or data assumptions changed significantly
- embeddings need a clean rebuild
- the application is being rebuilt on a fresh EC2 instance from scratch
