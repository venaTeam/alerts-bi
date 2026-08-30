"""The page's stylesheet and its one script.

Inlined rather than served as files: the pages are three of them, and a static-file mount
would be more machinery than the whole UI.
"""

from __future__ import annotations

__all__ = ["PAGE_CSS", "SUBMIT_SCRIPT"]

PAGE_CSS = """
:root { color-scheme: light dark; --line:#d0d7de; --muted:#57606a; }
body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  margin: 2.5rem auto; max-width: 46rem; padding: 0 1.25rem; line-height: 1.5; }
h1 { font-size: 1.3rem; margin-bottom: .25rem; }
p.sub { color: var(--muted); margin-top: 0; }
form { border: 1px solid var(--line); border-radius: 8px; padding: 1rem 1.25rem; margin: 1.5rem 0; }
label { display: block; font-size: .85rem; margin: .75rem 0 .2rem; }
select, input { font: inherit; padding: .4rem .5rem; width: 100%; box-sizing: border-box;
  border: 1px solid var(--line); border-radius: 6px; background: transparent; color: inherit; }
button { font: inherit; margin-top: 1.1rem; padding: .5rem 1.1rem; border-radius: 6px;
  border: 1px solid var(--line); cursor: pointer; background: transparent; color: inherit; }
button[disabled] { opacity: .6; cursor: progress; }
code { font-size: .85em; }
table { border-collapse: collapse; width: 100%; font-size: .86rem; margin-top: .5rem; }
th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--line); }
.note { color: var(--muted); font-size: .85rem; }
"""

#: The form posts JSON so the request body matches the documented contract exactly, rather
#: than a second form-encoded shape the endpoint would also have to accept. It also gives a
#: run some feedback: a plain form submit shows nothing for the minutes a run takes.
SUBMIT_SCRIPT = """
document.querySelector('form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector('button');
  button.disabled = true;
  button.textContent = 'Running\u2026';
  const data = Object.fromEntries(new FormData(form).entries());
  if (!data.run_at) delete data.run_at;
  const response = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/html' },
    body: JSON.stringify(data),
  });
  document.open();
  document.write(await response.text());
  document.close();
});
"""
