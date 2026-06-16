# neamt-core

Skill runtime for the Neamt AI platform. Install, sandbox, and orchestrate AI-powered skills with a CLI and web dashboard.

## Install

```bash
pip install -e ".[dev]"
```

## CLI

```bash
neamt install <github-url-or-local-path>
neamt list
neamt info <skill-id>
neamt enable <skill-id>
neamt disable <skill-id>
neamt config set anthropic_api_key <key>
neamt start          # dashboard on :8000
neamt doctor
```

## Permissions

| Permission | Description |
|---|---|
| `internet` | Outbound HTTP via `CoreAPI.http` |
| `filesystem:read` | Read from `~/.neamt/data/<id>/` |
| `filesystem:write` | Write to `~/.neamt/data/<id>/` |
| `anthropic_api` | Use Anthropic SDK via `CoreAPI.ai` |
| `system` | Elevated — requires explicit CONFIRM |

## Skill structure

```
my-skill/
├── neamt.manifest.json
└── main.py
```

`neamt.manifest.json`:
```json
{
  "id": "my-skill",
  "name": "My Skill",
  "version": "1.0.0",
  "author": "You",
  "description": "Does something cool",
  "permissions": ["internet"],
  "entry": "main.py",
  "neamt_version": "0.1.0",
  "dashboard": {
    "nav_label": "My Skill",
    "nav_icon": "🚀",
    "route": "/ui/my-skill",
    "ui": "ui/"
  }
}
```

## Directory layout

```
~/.neamt/
├── skills/           # installed skills
├── data/             # per-skill persistent storage
├── disabled-skills   # newline-separated list of disabled skill IDs
└── config.json       # encrypted config (API keys, etc.)
```
