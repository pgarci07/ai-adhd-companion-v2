# AI ADHD Companion

2025/26 TFG for Computer Science degree.

*AI ADHD Companion* is an empathetical and adaptative task manager for ADHD users.

Building blocks:

- Streamlit UI
- Supabase
- OpenAI API
- Docker

App is buit using basic layer isolation: UI / Application / Domain / Infrastructure

## Local execution

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app/ui/main.py
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app/ui/main.py
```

## Docker

```bash
docker build -f infra/docker/Dockerfile -t myapp .
docker run -p 8501:8501 --env-file .env myapp
```

## Current Status

At this point, project includes:

- basic project structure/scaffold
- simulated login
- basic chat with OpenAI
- contral configuration
- initial SQL for Supabase

Next ongoing iteration:

- real authentication via Supabase Auth
- app use cases: new task, show tasks
- real persistence of conversations (no chatbot)
- a more complete RLS
