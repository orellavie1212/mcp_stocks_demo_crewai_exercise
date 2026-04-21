# Windows Setup — Running the 4 Labs via WSL2

This repo's Makefile, shell scripts, and some CLI tools (`chmod`, `envsubst`, `gcloud`,
`terraform`) assume a POSIX shell. The cleanest way to run Labs 1–4 on Windows is
through **WSL2 + Ubuntu 24.04**. Inside WSL the commands are byte-for-byte the same
as on macOS/Linux.

> Assumption: your `.env` is already filled (in particular `GEMINI_API_KEY`).

---

## 0) Prerequisites on Windows (once)

```powershell
# In PowerShell as Administrator
wsl --install -d Ubuntu-24.04
# Reboot if prompted, then launch "Ubuntu" from Start menu and create your linux user.
```

Install **Docker Desktop** → Settings → Resources → **WSL Integration** → enable your
`Ubuntu-24.04` distro. Restart Docker Desktop.

> Windows-installed tools (choco-installed `terraform.exe`, `make.exe`, Anaconda, etc.)
> are Windows binaries and are **not** the tools invoked inside WSL. Install the
> linux-native versions inside Ubuntu as shown below.

---

## 1) One-time setup inside WSL (Ubuntu shell)

```bash
# Core tooling (Labs 1–3)
sudo apt update
sudo apt install -y make python3-venv python3-pip dos2unix git curl gnupg

# Lab 4 extras (skip if you only plan to do Labs 1–3)
sudo apt install -y gettext-base software-properties-common wget

# Terraform (HashiCorp apt repo)
wget -O- https://apt.releases.hashicorp.com/gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list

# gcloud CLI + kubectl (Google apt repo)
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
  | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list

sudo apt update
sudo apt install -y terraform google-cloud-cli \
                    google-cloud-cli-gke-gcloud-auth-plugin kubectl
```

---

## 2) Clone the repo **inside the WSL filesystem** (not `/mnt/c`)

```bash
cd ~
git clone https://github.com/zviba/mcp_stocks_demo_crewai_exercise.git
cd ~/mcp_stocks_demo_crewai_exercise
```

> Cloning under `/mnt/c/...` works but is 5–20× slower and causes Docker bind-mount
> permission issues. Keep the repo under your linux `$HOME`.

Bring your already-filled `.env` over and normalise line endings (Windows editors
often save as CRLF, which silently breaks bash env reads and causes 401s from Gemini):

```bash
# Option A — copy from your Windows drive, strip CRLF
cp /mnt/c/Users/<you>/mcp_stocks_demo_crewai_exercise/.env ./.env
dos2unix ./.env

# Option B — recreate fresh inside WSL
cp .env.example .env && nano .env     # paste GEMINI_API_KEY, save
```

---

## 3) Python env + deps (Labs 1–2 run from host Python)

The **linux** conda/venv inside the WSL distro is what matters here. A Windows-installed
Anaconda/Miniconda cannot be used from WSL — install conda *inside* Ubuntu if you want
to use it.

### Option A — conda (recommended if you already use conda)

One-time install of linux Miniforge inside WSL:

```bash
curl -L -o /tmp/miniforge.sh \
  https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash /tmp/miniforge.sh -b -p "$HOME/miniforge3"
"$HOME/miniforge3/bin/conda" init bash
exec bash
```

Create the project env and install deps:

```bash
conda create -n stock-agent python=3.11 -y
conda activate stock-agent
pip install -r requirements.txt \
            -r apps/mcp-server/requirements.txt \
            -r apps/job-api/requirements.txt \
            -r apps/agent-runtime/requirements.txt \
            -r apps/frontend-streamlit/requirements.txt
```

Each new WSL tab: `cd ~/mcp_stocks_demo_crewai_exercise && conda activate stock-agent`.

### Option B — venv (stdlib, no extra install)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt \
            -r apps/mcp-server/requirements.txt \
            -r apps/job-api/requirements.txt \
            -r apps/agent-runtime/requirements.txt \
            -r apps/frontend-streamlit/requirements.txt
```

Each new WSL tab: `cd ~/mcp_stocks_demo_crewai_exercise && source .venv/bin/activate`.

---

## 4) Lab 1 — Sync local (two tabs)

```bash
# Tab 1 — health-check API on :8001
uvicorn api:app --host 127.0.0.1 --port 8001

# Tab 2 — Streamlit UI on :8501
streamlit run streamlit_crewai_app.py
```

Open http://localhost:8501 in your Windows browser. WSL2 forwards localhost to Windows
automatically.

---

## 5) Lab 2 — Async HTTP (four tabs)

Every tab: `cd ~/mcp_stocks_demo_crewai_exercise` and activate your env (`conda activate stock-agent` or `source .venv/bin/activate`).

```bash
# Tab 1 — MCP server on :8001
make lab2-mcp

# Tab 2 — Job API on :8000
make lab2-api

# Tab 3 — Agent runtime (HTTP mode) on :8002
make lab2-worker

# Tab 4 — Streamlit UI on :8501
make lab2-ui
```

Open http://localhost:8501. The Makefile already does `-include .env` + `export`, so
`GEMINI_API_KEY` from `.env` flows into every target.

---

## 6) Lab 3 — Docker Compose

Make sure Docker Desktop is running and WSL integration is enabled for your distro.

```bash
make lab3-up         # docker compose --env-file .env -f docker/docker-compose.yml up --build -d
make logs            # tail logs from all services
make lab3-down       # stop
```

Service URLs:

| Service       | URL                             |
|---------------|---------------------------------|
| Streamlit UI  | http://localhost:8501           |
| Job API docs  | http://localhost:8000/docs      |
| MCP docs      | http://localhost:8001/docs      |
| Langfuse UI   | http://localhost:3000           |

### Rebuilding after a `requirements.txt` or `.env` change

`make lab3-up` uses `docker compose up --build`, which still reads from the pip
layer cache. If dependencies changed (or you just pulled new code) and the old
`llm.str / llm.BaseLLM Input should be a valid string` error comes back, force
a clean rebuild:

```bash
git pull                     # make sure pinned requirements.txt files are on disk
make lab3-rebuild            # down --rmi local, build --no-cache, up -d
```

Then verify the versions actually installed in the container match the pins:

```bash
docker compose -f docker/docker-compose.yml exec agent-runtime \
  pip show crewai langchain langchain-core langchain-google-genai \
  | grep -E '^(Name|Version)'
```

Expected: `crewai 1.10.1`, `langchain 1.2.12`, `langchain-core 1.2.18`,
`langchain-google-genai 4.2.1`.

---

## 7) Lab 4 — GCP

Make sure `.env` already has the right values (they were copied from `.env.example` and
the Makefile auto-loads them via `-include .env` + `export`):

```bash
grep -E '^(GCP_PROJECT|GCP_REGION|GEMINI_API_KEY)=' .env
```

Authenticate once. On WSL, use `--no-launch-browser` for the ADC flow — it avoids the
flaky WSL-to-Windows browser redirect and just prints a URL you paste into any browser:

```bash
gcloud auth login                                     # interactive, opens browser
gcloud auth application-default login --no-launch-browser   # paste URL, paste code back
```

Then run the labs directly (no need to `export` or pass `GCP_PROJECT=` on the CLI — the
Makefile reads them from `.env`):

```bash
make setup-gcp        # provisions GCP infra + deploys all services
make show-urls        # prints Cloud Run URLs

# Teardown when done (stops billing)
make teardown         # interactive: asks for confirmation
# or for unattended / CI runs:
# make teardown-yes
```

`setup-gcp` reads `GEMINI_API_KEY` from your `.env` and seeds it into GCP Secret
Manager; you don't need to paste it anywhere else.

> If for some reason you want to override the project/region for one run without
> editing `.env`, you can still do: `make setup-gcp GCP_PROJECT=other-proj GCP_REGION=europe-west1`.

---

## 8) Troubleshooting

- **401 from Gemini (all labs)** → `.env` saved with CRLF line endings. Fix:
  ```bash
  dos2unix .env
  ```
- **Pydantic bool/int parse error at startup** (e.g. `input_value='false  '`, `bool_parsing`) → your `.env` has trailing whitespace in a value, usually because a line was copied with a `KEY=value  # comment` still attached. `python-dotenv` strips the `# comment` but not the spaces before it, so pydantic receives `'false  '` and rejects it. Fix:
  ```bash
  # Option A — recreate .env from the cleaned .env.example
  cp .env.example .env && nano .env      # re-paste GEMINI_API_KEY
  # Option B — strip inline comments in-place
  sed -i 's/[[:space:]]*#.*$//' .env
  ```
- **`docker` not found inside WSL** → Docker Desktop → Settings → Resources → WSL
  Integration → toggle your distro on → restart Docker Desktop.
- **`make`, `conda`, or `terraform` not found** → you are running the Windows binary
  (e.g. `make.exe` from choco) instead of the linux one. Open a fresh Ubuntu shell and
  verify with `which make` (should be `/usr/bin/make`).
- **Port already in use (8501/8000/8001)** → close the conflicting Windows app (often a
  Windows-side Python/Streamlit already running).
- **Very slow installs or Docker bind-mount permission errors** → the repo is under
  `/mnt/c/...`. Move it to `~/` inside WSL and re-run.
- **`gcloud auth login` hangs / can't open a browser** → install `wslu` which provides
  `wslview`, or pass `--no-browser`:
  ```bash
  sudo apt install -y wslu
  # or
  gcloud auth login --no-browser
  ```
- **`gcloud auth application-default login` fails in WSL** (often even when
  `gcloud auth login` works) → the ADC flow tries to spin up a local HTTP server that
  the Windows browser can't reach. Use the no-browser variant — it prints a URL you
  open in any browser and then paste the auth code back:
  ```bash
  gcloud auth application-default login --no-launch-browser
  ```
- **Conda env inactive after opening a new tab** → ensure `conda init bash` ran once
  and restart the shell (`exec bash`).
- **Is my `conda` the Windows one or the WSL one?** A Windows-installed Anaconda /
  Miniconda and its envs cannot be used from inside WSL (different OS binaries).
  Check from an Ubuntu shell:
  ```bash
  which conda
  conda info | grep -E 'platform|base environment'
  ```
  - Good (reusable inside WSL): path like `/home/<you>/miniforge3/bin/conda` and
    `platform : linux-64`.
  - Not reusable: path under `/mnt/c/...` (e.g. `/mnt/c/Users/<you>/anaconda3/Scripts/conda`)
    or `platform : win-64` — that's your Windows conda. Install linux Miniforge inside
    WSL using the snippet in section 3 above and recreate the env there; both condas
    can coexist without conflict.
- **Lab 3 worker keeps throwing `llm.str` / `llm.BaseLLM` validation errors** after
  `make lab3-up` → `requirements.txt` on disk still has the old unpinned CrewAI/LangChain
  (either your branch is behind or a local edit was never committed). Check:
  ```bash
  grep -E '^(crewai|langchain)' apps/agent-runtime/requirements.txt
  ```
  You should see `crewai==1.10.1`, `langchain==1.2.12`, `langchain-core==1.2.18`,
  `langchain-google-genai==4.2.1`. If not, `git pull` (or commit your local pins),
  then run `make lab3-rebuild`.
- **Lab 4 `make setup-gcp` fails at `terraform apply` with
  `Cloud Firestore API has not been used in project ... SERVICE_DISABLED`** →
  Terraform's `import` block for the Firestore default DB runs during plan/refresh,
  before any APIs are enabled. Bootstrap them manually once, then re-run:
  ```bash
  gcloud services enable firestore.googleapis.com \
                         serviceusage.googleapis.com \
                         cloudresourcemanager.googleapis.com \
                         compute.googleapis.com \
    --project=<your-project-id>
  # wait ~30s for propagation, then:
  make setup-gcp
  ```
  If you pulled the latest `scripts/setup-gcp.sh`, this is already handled
  automatically as Step 1.5. Subsequent runs on the same project are no-ops.
- **Lab 4 `make setup-gcp` fails at `terraform apply` with
  `Cannot import non-existent remote object` on `google_firestore_database.default`**
  → On a brand-new project the Firestore default DB doesn't exist yet, but
  Terraform's `import` block in `main.tf` is strict. Pre-create the DB
  manually once, then re-run:
  ```bash
  gcloud firestore databases create \
    --location=<your-region> \
    --database='(default)' \
    --type=firestore-native \
    --project=<your-project-id>
  make setup-gcp
  ```
  If you pulled the latest `scripts/setup-gcp.sh`, this is handled
  automatically as Step 1.6 (idempotent).
- **Lab 4 `make setup-gcp` exits with `timed out waiting for the condition`
  on the agent-runtime rollout** → GKE Autopilot first-boot provisions
  nodes on-demand and routinely takes 4-8 minutes. If you pulled the
  latest script this is already non-fatal (10 min + soft-fail). The app
  is still deployed; check with:
  ```bash
  kubectl get pods -n stock-agent -w
  make show-urls
  ```
- **Lab 4 `make teardown` fails at `google_compute_subnetwork.subnet` with
  `resourceInUseByAnotherResource` pointing at `serverless-ipv4-...`** →
  Cloud Run Direct VPC Egress (`--network/--subnet/--vpc-egress`) leaves a
  hidden `serverless-ipv4-<id>` compute address behind when services are
  deleted async. Terraform doesn't know about it, so the subnet destroy
  fails while the address still references it. The latest
  `scripts/teardown-gcp.sh` waits for the service deletes and cleans these
  up automatically (Step 1b). On an older copy, clean up manually and
  re-run:
  ```bash
  PROJECT=<your-project-id>
  REGION=us-central1
  gcloud compute addresses list --project="$PROJECT" \
    --filter="name ~ ^serverless-ipv4 AND region:$REGION" \
    --format="value(name)" | while read -r addr; do
      gcloud compute addresses delete "$addr" \
        --region="$REGION" --project="$PROJECT" --quiet
    done
  make teardown-yes GCP_PROJECT="$PROJECT" GCP_REGION="$REGION"
  ```
