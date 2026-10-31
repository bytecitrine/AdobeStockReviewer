# Adobe Stock AI Reviewer

Reviews your AI-generated images like a strict Adobe Stock reviewer **before** you upload them.
Drop images into a folder; each one is measured, reviewed by an AI model through
[OpenRouter](https://openrouter.ai), scored, and filed into `Ready_To_Upload`, `Needs_Editing`
or `Rejected` with a detailed report beside it.

## Quick start (Windows, PowerShell, uv)

```
uv venv --python 3.12
uv pip install -r requirements-dev.txt
uv pip install -e .
```

1. Copy `config\.env.example` to `config\.env` and paste your OpenRouter key.
2. Add credits at https://openrouter.ai/settings/credits (a new account has none, which causes error 402).
3. Check the setup (no API call, no cost): `uv run python -m adobe_reviewer --check`
4. Put some JPEGs (4 MP or larger) in `images\Incoming\`, then: `uv run python -m adobe_reviewer --once`
5. Keep it running and watching: `uv run python -m adobe_reviewer` (Ctrl+C stops it after the current image).

## Where things are

```
AdobeStockReviewer/
├── config/            YOURS  settings.json (model, thresholds) and .env (your key, never share it)
├── prompts/           YOURS  the reviewer's instructions, plain text: edit to change strictness
├── images/            YOURS  Incoming/ -> Ready_To_Upload/ | Needs_Editing/ | Rejected/
├── reports/           json/ and txt/ copies of every report
├── logs/              reviewer.log (rotating)
├── src/adobe_reviewer/   CODE  (see "How it works")
├── tests/                CODE  automated tests: run with `uv run pytest`
└── pyproject.toml, requirements*.txt, uv.lock    project files
```

You only ever work in `config`, `prompts`, `images`, `reports` and `logs`. Folders named `.venv`,
`.idea`, `.pytest_cache`, `__pycache__` and `*.egg-info` are generated: ignore them.

## What you get for each image

| Result | When | Folder |
|---|---|---|
| PASS | score 90 or more, no critical/major issues, model approved it, confidence 0.6 or more | `Ready_To_Upload` |
| NEEDS EDITING | score 75-89, or a would-be pass that failed one of those safeguards | `Needs_Editing` |
| FAIL | score below 75, or any hard-fail issue (logo, watermark, resolution outside 4-100 MP, ...) | `Rejected` |

Next to each image (and in `reports\`) you get `name_review.txt` (read this one) and
`name_review.json` (same data for programs): scores, every issue with its explanation and exact fix,
recommended fixes in priority order, why it went to that folder, and the measured facts.

Images under 4 MP, or over 100 MP, are rejected immediately **without calling the AI**, so they cost nothing.
If a name is already taken, the new file becomes `name_1.jpg` (reports match); nothing is ever overwritten.

## Settings you will actually change (`config\settings.json`)

| Setting | What it does |
|---|---|
| `pipeline.agents[0].model` | The OpenRouter model id, for example `google/gemini-2.5-pro`. Any vision-capable model works. Applies to the next image, no restart. |
| `pipeline.agents[0].response_format` | `json_object` (default), `json_schema`, or `none` for models that reject JSON mode. |
| `thresholds` | `ready` (90), `needs_editing` (75), `min_confidence` (0.6). |
| `report_placement` | `both`, `beside_image` or `reports_dir`. |
| `image.min_megapixels` / `max_megapixels` | Adobe's limits (4 and 100). |
| `watcher.failure_retry_cooldown_seconds` | How long a failed image waits before a retry (300). |
| `watcher.process_existing_on_start` | `false` ignores images already in Incoming when the app starts. |

A mistake in `settings.json` never crashes a running app: it logs the error and keeps the previous settings.

## Changing how strict the reviewer is

Edit `prompts\v1_reviewer.system.md` (the **SCORING** and **HARD FAIL** sections matter most). Changes apply to the
next image. Adobe's rules change over time, so re-read the hard-fail list occasionally. Your own
experience of what gets accepted is the best calibration.

## Troubleshooting

| You see | Meaning and fix |
|---|---|
| `HTTP 402 insufficient OpenRouter credits` | Add credits at openrouter.ai/settings/credits. The image stays in Incoming. |
| `HTTP 401 invalid or missing API key` | Check `config\.env`. The key must be on the same account that has credits. |
| `HTTP 404 model or endpoint not found` | Wrong model id, or the model cannot read images. Pick another in `settings.json`. |
| `HTTP 400 ... response_format` | The model rejects JSON mode. Set `response_format` to `none` for that agent. |
| `processing is paused for 60s` | A problem that affects every image (key, credits, model, prompts). Fix it; the app resumes by itself. |
| `Environment variable OPENROUTER_API_KEY is not set` | Create `config\.env` from `config\.env.example`. |
| Nothing happens to an image | It must be a supported type (jpg, jpeg, png, webp) and finish copying (2 seconds unchanged). Check `logs\reviewer.log`. |
| An image stays in Incoming | The review failed (see the log); it retries after the cool-down, or replace the file to retry at once. |
| `locked by another program` | Close any app showing the image (preview pane, editor); the move retries by itself. |
| `File cannot be opened as an image` | The file is corrupt or incomplete; it is rejected with a report. Re-export it. |

## Cost

Each AI review costs a few cents, depending on the model. The log shows tokens and the exact cost per image,
and the last log line summarises the session. Cheaper vision models are a one-line change in `settings.json`.

## How it works

```
Incoming/ -> FolderWatcher -> ReviewEngine -> DecisionEngine -> ReviewPublisher -> folder + reports
              (ready files)   measure locally,    your score       names, reports,
                              preflight, AI       rules in code    then the move
```

| Package (`src/adobe_reviewer/`) | Job |
|---|---|
| `config/` | Loads and validates `settings.json`; resolves folders; reads the API key |
| `analysis/` | Measures the original file (resolution, sharpness, noise, JPEG quality); prepares the AI copy; rule-based preflight |
| `clients/` | OpenRouter client (retries, usage and cost tracking) and JSON extraction |
| `prompts/` | Loads prompt files |
| `agents/` | Reviewer agents; `BaseAgent` handles the call, JSON validation and one automatic repair |
| `engine/` | `AgentPipeline`, `ReviewEngine`, `DecisionEngine` |
| `reporting/` | Report writer and the publisher that files results safely |
| `io/` | Moves files without ever overwriting |
| `watcher/` | Finds images that have finished copying |
| `app.py`, `main.py` | The loop and the command line |

## Tests

`uv run pytest` runs every test; none of them call the real AI or cost anything.

## Roadmap (Version 2)

Technical Inspector, Commercial Inspector and a Final Adobe Judge as three cooperating agents. The architecture
already supports it: each is a small `BaseAgent` subclass with its own prompt files and its own model entry in
`settings.json`, plus a new pipeline mode in `engine/pipeline.py`.
