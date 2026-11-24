# ACEest Fitness & Gym

ACEest Fitness is a Flask application for managing gym clients, memberships, workout plans, workout history, progress, and fitness metrics. The repository continues the Assignment 1 application with the Assignment 2 quality, container, CI/CD, and deployment work.

## Application and architecture

- Flask application and Jinja templates serve the UI and `/health` endpoint.
- SQLite stores users, clients, progress, workouts, exercises, and metrics.
- The default development database is under `instance/`; the container and Kubernetes configurations keep runtime data under `/data`.
- The Docker runtime uses Gunicorn, a non-root account, a read-only root filesystem, a writable data volume, and an HTTP health check.
- Jenkins and GitHub Actions run linting and tests. The Docker test target runs the same pytest suite.

SQLite is a single-writer, local-file database. The supported Kubernetes application deployment therefore uses **one replica** and does not create an overlapping pod during a rolling update. Blue/green switching is sequential and has downtime. Canary and A/B templates are read-only demonstrations with isolated ephemeral databases; they are not production configurations for persistent application data.

## Assignment 1 application features

- Login and session-based access control.
- Client profiles and fitness-program selection.
- Membership tracking.
- Workout logging and exercise tracking.
- Progress and body-metric records.
- Fitness dashboard and reports.
- SQLite persistence and application health endpoint.

## Technology

- Python 3.12 in the Docker image and GitHub Actions.
- Python 3.14.10 on the Windows Jenkins agent used by the recorded successful Build #47.
- Flask, Gunicorn, SQLite, pytest, pytest-cov, Ruff, Docker Buildx, Jenkins, Kubernetes, and optional Istio routing resources.

The Jenkins host and container Python versions intentionally differ. Build #47 exercised the Jenkins host on Python 3.14.10 and the Docker test image on Python 3.12. Dependencies should be changed only after compatibility is verified for both environments.

## Repository structure

```text
app.py                         Flask application and business logic
tests/                         Unit, application, and manifest tests
templates/                     Jinja views
static/                        Stylesheets and browser assets
Dockerfile                     Python 3.12 runtime and test targets
.github/workflows/main.yml     GitHub Actions CI
Jenkinsfile                    Parameter-controlled Windows Jenkins pipeline
k8s/                           Rolling, blue/green, canary, shadow, and A/B examples
kubernetes/                    Assignment Deployment and Service manifests
scripts/                       Deployment, rollback, and health-check helpers
report/                        Assignment 2 report
reports/                       Assignment submission report
```

## Git and GitHub workflow

The established branch is `main`. For changes, create a focused feature branch, run the local checks, open a pull request, and merge through the repository's normal review process. GitHub Actions runs on pushes and pull requests; the Jenkins multibranch job checks out its configured branch and `Jenkinsfile`.

```powershell
git switch -c feature/short-description
git status --short
git diff --check
git diff
```

Never commit local virtual environments, databases, coverage output, `.env` files, credentials, or Kubernetes Secret manifests. Use a protected local environment file or a managed secret store for values.

## Local setup

Use Python 3.12–3.14. Python 3.12 is the container and GitHub Actions target.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python app.py
```

Open `http://127.0.0.1:5000`. Configure the initial administrator and Flask session key through environment variables; never place real values in source control. The development database path can be set with `DATABASE_PATH` or `ACEEST_DB_PATH`.

## Tests and coverage

```powershell
python -m compileall -q app.py tests
python -m ruff check app.py tests
python -m pytest -q --junitxml=reports/pytest-results.xml
python -m pytest -q --cov=app --cov-report=xml:coverage.xml --cov-report=html:coverage_html --cov-report=term-missing
```

The Jenkins pipeline archives JUnit and coverage reports. `.gitignore` excludes generated coverage, pytest, bytecode, local database, and temporary files.

## Docker build and runtime check

Docker Desktop must be running with the Linux container engine. Buildx is preferred; the Jenkins pipeline falls back to `docker build` with BuildKit enabled and does not support the legacy builder.

```powershell
$sha = (git rev-parse HEAD).Trim()
$tag = "sha-$sha"
$docker = (Get-Command docker.exe -ErrorAction Stop).Source

docker buildx build --load --target test --tag aceest-fitness:test-local .
docker run --rm aceest-fitness:test-local

docker buildx build --load --target runtime --build-arg "APP_VERSION=$tag" --tag "aceest-fitness:$tag" .
.\scripts\check-container-health.ps1 -DockerExe $docker -Image "aceest-fitness:$tag"
```

The runtime image contains a Docker `HEALTHCHECK`; the script additionally waits for the container health state and calls `/health`. The `sha-<40-character-commit>` tag is the deployment identity. Release aliases are optional and must not replace the SHA tag.

## GitHub Actions

`.github/workflows/main.yml` runs syntax validation, Ruff, pytest, a runtime image build, container tests, and an HTTP health check for pushes and pull requests. Docker jobs use Buildx and load images locally; they do not publish or deploy.

## Jenkins pipeline

The Jenkinsfile is written for the configured Windows Jenkins worker: Python setup uses PowerShell and Windows virtual-environment paths, while CLI stages use `bat` and PowerShell. The Windows worker must have Python 3.12–3.14, Git, Docker Desktop's Linux engine, Docker Buildx (or a BuildKit-capable `docker build`), and `kubectl` for deployment. Jenkins currently connects to Docker Desktop through `npipe:////./pipe/dockerDesktopLinuxEngine`.

Pipeline stages:

1. Checkout the configured SCM branch.
2. Verify Python and create `.venv`.
3. Install development dependencies.
4. Compile Python and run Ruff.
5. Run pytest with JUnit output.
6. Generate coverage XML and HTML reports.
7. Run SonarQube analysis and wait for its quality gate when the plugin/server are configured.
8. Build the runtime and test images with Buildx where available.
9. Run container tests and runtime health verification.
10. Authenticate to Docker Hub and push the immutable SHA tag only when `PUBLISH_IMAGE=true`; an additional `IMAGE_VERSION` alias requires `PUBLISH_RELEASE_TAG=true`.
11. Deploy the published SHA to Kubernetes only when `DEPLOY_TO_K8S=true`; rollout and health failures trigger a rollback attempt.

`PUBLISH_IMAGE`, `PUBLISH_RELEASE_TAG`, `DEPLOY_TO_K8S`, and `DOCKER_AUTH_DIAGNOSTIC` all default to `false`. Deployment requires image publishing; the authentication diagnostic cannot be combined with publishing or deployment. Optional stages are reported as skipped when disabled.

### SonarQube prerequisites

The last Jenkins audit found the SonarQube Scanner plugin/server configuration unavailable. In that state the analysis and quality-gate stages are skipped and are **not a pass**. Configure the plugin and the Jenkins server named `sonarqube` before expecting analysis. Once analysis is active, a failed quality gate aborts the pipeline.

### Docker Hub publishing

The Jenkins credential ID `dockerhub-pat-working` was present in the last credential audit; this does not prove that the token is valid. The pipeline uses it only inside the publishing or explicitly requested diagnostic stage, pipes it to `docker login --password-stdin`, uses a temporary Docker config directory, and removes that directory afterward. No token belongs in Git or a command-line argument.

To publish, first confirm the credential and repository access with the repository administrator, then run the Jenkins build with:

- `PUBLISH_IMAGE=true`
- `PUBLISH_RELEASE_TAG=false` unless a mutable alias is intentionally needed
- `DEPLOY_TO_K8S=false` unless the cluster prerequisites below are ready
- `DOCKER_AUTH_DIAGNOSTIC=false`

The immutable image is pushed as `IMAGE_REPO:sha-<full-commit-SHA>`. The optional `IMAGE_VERSION` alias is secondary.

## Kubernetes prerequisites and supported deployment

The supported workload uses a single SQLite-backed pod, a `ReadWriteOnce` PVC, readiness/startup/liveness probes, resource requests and limits, and a ClusterIP Service. `maxSurge: 0` prevents two application pods from mounting the SQLite volume at once; updates can therefore have brief downtime. The Kubernetes templates use a zero-SHA sentinel image which must be replaced by the deployment helper; do not apply the rolling manifest directly.

Before deploying, provide a working cluster context, `kubectl`, a storage class that supports the PVC, and a Secret named `aceest-secrets` containing keys `admin-username`, `admin-password`, and `flask-secret-key`. Keep the protected source file outside the repository:

```powershell
kubectl config current-context
$secretFile = Read-Host "Path to the protected environment file"
kubectl create secret generic aceest-secrets --from-env-file="$secretFile"
```

Build and publish the exact commit image, then deploy that immutable tag:

```powershell
$sha = (git rev-parse HEAD).Trim()
$image = "ruchitabits/aceest-fitness:sha-$sha"
.\scripts\deploy-k8s.ps1 -Image $image
```

The deployment helper applies the ConfigMap and PVC, renders the Deployment with the selected SHA image, waits for rollout, checks Service endpoints and `/health`, and attempts `kubectl rollout undo` plus health verification if rollout or health verification fails. Rollback requires a previous Deployment revision. Manual rollback:

```powershell
.\scripts\rollback-k8s.ps1
```

The Jenkins credential ID `kubeconfig` was not present in the last audit. Kubernetes deployment remains unavailable until that credential and a reachable cluster are provisioned. The current workspace has no verified Kubernetes context; no deployment has been run.

## Deployment strategy examples

All manifests reference an immutable SHA-shaped sentinel until a helper or `kubectl set image` supplies a real published tag. The advanced Istio examples are not executed in this environment.

### 1. Rolling update

`k8s/rolling-update.yaml` and `scripts/deploy-k8s.ps1` provide the supported single-replica SQLite update. The old pod is stopped before its replacement starts. Verify with `kubectl rollout status` and `/health`; use `scripts/rollback-k8s.ps1` to restore the previous revision. Brief downtime is expected.

### 2. Blue-green

`k8s/deployment-blue.yaml`, `k8s/deployment-green.yaml`, and `k8s/service.yaml` define two color tracks and a selector-based Service. Both color Deployments start at zero replicas. `scripts/switch-blue-green.ps1 -Target <blue|green> -Image <repository:sha-40hex>` drains and stops the previous color before starting the target, then switches the Service and checks health. If the switch or health check fails, it attempts to restore the previous color and Service selector. Because both colors use the same SQLite PVC, this is a **sequential, downtime handoff**, not simultaneous blue/green capacity.

### 3. Canary

`k8s/canary.yaml` defines Istio weighted routing (90% stable / 10% canary) and disabled-by-default workloads. Both workloads use isolated ephemeral storage and `SHADOW_MODE=true`, so they are suitable only for non-mutating demonstration requests such as `/health`. Istio and an ingress gateway are required. Do not use this as a production canary for writes or persistent user data.

After an Istio-enabled cluster is prepared for this read-only demonstration, set real SHA images, scale both deployments to one, wait for readiness, and adjust traffic with:

```powershell
.\scripts\set-canary-weight.ps1 -CanaryPercent 10
```

Set the canary percentage to `0` to route all requests back to stable. A production canary requires the application to use a shared external database; that database integration is not implemented.

### 4. Shadow

`k8s/shadow.yaml` mirrors traffic through Istio from a primary instance to a copy. The primary is the only SQLite-backed writer; the copy uses `emptyDir` and `SHADOW_MODE=true`, which suppresses state-changing requests. Configure the real SHA image and both required services before applying. Verify the mirror's health/logs, not its database state. Disable mirroring by removing or setting the VirtualService mirror to zero. This is safe for observation but does not provide production-like shared data.

### 5. A/B

`k8s/ab-testing.yaml` routes requests with `x-aceest-group: beta` to version B and other requests to version A. The deployments are disabled initially and use isolated ephemeral storage plus `SHADOW_MODE=true`; this makes read-only routing tests safe with SQLite but not representative for writes or persistent user records. Istio and an ingress gateway are required. Roll back by routing the VirtualService only to subset `a` and scaling B to zero. Real A/B business testing requires a shared external datastore and is not implemented.

## Assignment requirement mapping

The official assignment specification files were not present in the repository during this review; this mapping uses the requirements supplied in the task.

| Requirement | Implementation | Status / limits |
|---|---|---|
| Preserve Assignment 1 application behavior | `app.py`, templates, static assets, and tests retained | Preserved; no business logic rewrite |
| Source control and CI trigger | GitHub Actions workflow and Jenkins SCM pipeline | Configured; no new run triggered by these local edits |
| Lint, tests, JUnit, coverage | Jenkinsfile, `requirements-dev.txt`, pytest configuration | Existing Build #47 passed 32 tests and reported 92%; rerun the updated suite before submission |
| SonarQube and quality gate | Jenkinsfile and `sonar-project.properties` | Not configured in Jenkins as of the last audit |
| Container build, tests, health | Dockerfile, Buildx CI steps, health endpoint and check script | Build #47 built both images and ran 32 container tests; new health step requires local validation |
| Immutable image publishing | Jenkins SHA tagging and optional release alias | Implemented; not executed; credential validity and registry access remain external |
| SQLite-safe Kubernetes rolling update | Single replica, RWO PVC, no surge, probes, resource limits, health/rollback scripts | Implemented; not deployed because cluster context/credential is unavailable |
| Blue-green, canary, shadow, A/B examples | Kubernetes manifests, switch/weight scripts, and this guide | Implemented as described; canary/A/B are read-only demonstrations, not persistent production strategies |
| Assignment evidence | Evidence checklist below | Capture genuine screenshots/logs only after running the corresponding stage |

## Submission evidence checklist

- [ ] GitHub Actions run for the final commit, with lint, tests, container tests, and health check.
- [ ] Jenkins build for the final commit, with the console output and archived JUnit/coverage reports.
- [ ] SonarQube analysis and quality-gate result, if the Jenkins plugin/server is configured.
- [ ] Docker Hub repository showing the immutable SHA tag, if publishing is authorized.
- [ ] Kubernetes rollout, service health, and rollback evidence, only after cluster and credentials are available.
- [ ] Istio routing evidence for each requested strategy, clearly identifying read-only demo limitations.

Do not present a skipped stage, unavailable service, or unexecuted deployment as a pass.

## Troubleshooting

- **Docker pipe/engine unavailable:** start Docker Desktop's Linux engine and verify `docker info` under the Jenkins service identity; a working interactive CLI alone does not prove Jenkins access.
- **Buildx unavailable:** the pipeline tries BuildKit-enabled `docker build`; Docker Engine must support BuildKit. Legacy builder mode is intentionally unsupported.
- **Python setup fails:** set `PYTHON_EXE` to an existing Python 3.12, 3.13, or 3.14 executable visible to the Jenkins service account.
- **Sonar stages skip:** configure the SonarQube Scanner plugin and Jenkins server named `sonarqube`; a skip is not a quality-gate pass.
- **Image pull fails:** confirm the SHA tag was pushed and the cluster can pull the repository; do not substitute a mutable tag for the deployment image.
- **PVC or rollout stalls:** check the storage class, `ReadWriteOnce` binding, `kubectl describe pod`, and events. Keep one writer for SQLite.
- **Kubernetes deployment is unavailable:** confirm `kubectl config current-context`, a reachable cluster, and the Jenkins `kubeconfig` credential. Do not enable deployment until all are confirmed.
