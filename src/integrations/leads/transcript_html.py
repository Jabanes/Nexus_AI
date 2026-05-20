"""Render a session JSON transcript into a clickable HTML viewer.

Excel cells with a file:// link open this HTML in the default browser —
much nicer than opening a JSON in Notepad.
"""

import html
from pathlib import Path
from typing import Any, Dict


def render_transcript_html(session_data: Dict[str, Any], dest_path: Path) -> Path:
    meta = session_data.get("meta", {})
    summary = session_data.get("summary", {})
    transcript = session_data.get("transcript", [])

    tenant = html.escape(str(meta.get("tenant_id", "")))
    session_id = html.escape(str(session_data.get("session_id", "")))
    duration = meta.get("duration_seconds", 0)
    phone = html.escape(str(meta.get("customer_phone", "") or ""))
    termination = html.escape(str(meta.get("termination_reason", "") or ""))
    summary_text = html.escape(str(summary.get("transcript_summary", "") or ""))

    rows = []
    for entry in transcript:
        role = entry.get("role", "")
        etype = entry.get("type", "")
        ts = entry.get("timestamp", "")
        if etype == "text":
            content = html.escape(str(entry.get("content", "")))
            css = "user" if "user" in role or role == "customer" else ("agent" if "agent" in role or "ai" in role else "system")
            rows.append(f'<div class="msg {css}"><span class="ts">{ts}s</span><span class="role">{html.escape(role)}</span><div class="content">{content}</div></div>')
        elif etype == "tool_call":
            name = html.escape(str(entry.get("name", "")))
            params = html.escape(str(entry.get("input", "")))
            rows.append(f'<div class="msg tool"><span class="ts">{ts}s</span><span class="role">tool_call</span><div class="content"><code>{name}({params})</code></div></div>')
        elif etype == "tool_result":
            content = html.escape(str(entry.get("content", ""))[:500])
            rows.append(f'<div class="msg tool"><span class="ts">{ts}s</span><span class="role">tool_result</span><div class="content"><code>{content}</code></div></div>')

    body = "\n".join(rows) if rows else "<p><em>No transcript content.</em></p>"

    htmldoc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Transcript {session_id}</title>
<style>
body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em; color: #222; }}
header {{ border-bottom: 1px solid #ddd; padding-bottom: 1em; margin-bottom: 1em; }}
header h1 {{ margin: 0 0 .25em 0; font-size: 1.3em; }}
header .meta {{ color: #666; font-size: .9em; }}
.summary {{ background: #f4f7fb; border-left: 3px solid #4a90e2; padding: .75em 1em; margin: 1em 0; border-radius: 3px; }}
.msg {{ margin: .8em 0; display: grid; grid-template-columns: 60px 80px 1fr; gap: .75em; align-items: start; }}
.msg .ts {{ color: #999; font-family: monospace; font-size: .85em; }}
.msg .role {{ font-weight: 600; font-size: .85em; }}
.msg.user .role {{ color: #4a90e2; }}
.msg.agent .role {{ color: #2ecc71; }}
.msg.tool .role {{ color: #f39c12; }}
.msg.system .role {{ color: #999; }}
.content {{ line-height: 1.4; }}
code {{ background: #f4f4f4; padding: .15em .35em; border-radius: 3px; font-size: .9em; }}
</style></head><body>
<header>
  <h1>Conversation Transcript</h1>
  <div class="meta">Tenant: {tenant} &nbsp;|&nbsp; Session: {session_id} &nbsp;|&nbsp; Duration: {duration}s &nbsp;|&nbsp; Phone: {phone} &nbsp;|&nbsp; Termination: {termination}</div>
</header>
{f'<div class="summary"><strong>Summary:</strong> {summary_text}</div>' if summary_text else ''}
<section>
{body}
</section>
</body></html>"""

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(htmldoc, encoding="utf-8")
    return dest_path
