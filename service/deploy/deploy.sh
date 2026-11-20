#!/usr/bin/env bash
# Deploy the service to Cloud Run. Creates and changes billable cloud resources: read
# service/deploy/README_DEPLOY.md first. Run from the repository root:
#   PROJECT_ID=my-project REGION=us-central1 service/deploy/deploy.sh
# Steps (all idempotent, safe to re-run):
#   1. terraform apply for the APIs, image repository and the (empty) database-URL secret
#   2. stop unless the secret has a value (added by hand, see README_DEPLOY.md)
#   3. build the image for linux/amd64 and push it, tagged with the git commit
#   4. terraform apply for everything else
#   5. run migrations, then roll the new image out to the API and the worker job
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
TF_DIR="service/deploy/terraform"
TF_VARS=(-var "project_id=$PROJECT_ID" -var "region=$REGION")

for tool in gcloud docker terraform git; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool"; exit 1; }
done
[[ -z "$(git status --porcelain)" ]] || { echo "commit or stash changes first"; exit 1; }

echo "This deploys to project '$PROJECT_ID' in $REGION and may incur charges."
read -r -p "Type the project id to continue: " answer
[[ "$answer" == "$PROJECT_ID" ]] || { echo "aborted"; exit 1; }

repo="$REGION-docker.pkg.dev/$PROJECT_ID/p2s"
image="$repo/p2s-service:$(git rev-parse --short=12 HEAD)"

terraform -chdir="$TF_DIR" init -input=false
terraform -chdir="$TF_DIR" apply "${TF_VARS[@]}" -var "image=$image" \
  -target=google_project_service.enabled \
  -target=google_artifact_registry_repository.images \
  -target=google_secret_manager_secret.database_url

if [[ -z "$(gcloud secrets versions list p2s-database-url --project "$PROJECT_ID" \
      --filter=state=ENABLED --limit=1 --format='value(name)')" ]]; then
  echo "Secret p2s-database-url has no value yet. Add the Neon connection string"
  echo "(see README_DEPLOY.md, step 2), then re-run this script."
  exit 1
fi

gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
# Cloud Run runs linux/amd64; a build on an Apple-silicon machine defaults to arm64.
docker buildx build --platform linux/amd64 -f service/Dockerfile -t "$image" --push .

terraform -chdir="$TF_DIR" apply "${TF_VARS[@]}" -var "image=$image"

# Migrations first, so the new API and worker never run against an old schema.
gcloud run jobs update p2s-migrate --image "$image" --region "$REGION" --project "$PROJECT_ID"
gcloud run jobs execute p2s-migrate --wait --region "$REGION" --project "$PROJECT_ID"
gcloud run deploy p2s-api --image "$image" --region "$REGION" --project "$PROJECT_ID"
gcloud run jobs update p2s-worker --image "$image" --region "$REGION" --project "$PROJECT_ID"

echo "Deployed $image"
terraform -chdir="$TF_DIR" output api_url
