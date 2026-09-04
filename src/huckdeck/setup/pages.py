"""HTML for the setup pages. Plain forms: captive-portal browsers are limited."""

from __future__ import annotations

from html import escape

from ..client import Child
from .wifi import Network

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font: 17px/1.45 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
       background: #f4f4f5; color: #18181b; }
@media (prefers-color-scheme: dark) { body { background: #18181b; color: #f4f4f5; } }
main { max-width: 480px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
.sub { opacity: .7; margin: 0 0 20px; }
.card { background: rgba(127,127,127,.12); border-radius: 12px; padding: 4px 0; margin: 0 0 16px; }
label.net { display: flex; align-items: center; gap: 12px; padding: 12px 16px; cursor: pointer;
            border-top: 1px solid rgba(127,127,127,.2); }
label.net:first-child { border-top: 0; }
label.net input { width: 20px; height: 20px; margin: 0; }
.name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-size: .85rem; opacity: .6; font-variant-numeric: tabular-nums; }
.field { margin: 0 0 16px; }
.field label { display: block; font-size: .9rem; opacity: .75; margin-bottom: 6px; }
input[type=text], input[type=password] { width: 100%; font-size: 17px; padding: 12px;
    border-radius: 10px; border: 1px solid rgba(127,127,127,.4); background: transparent; color: inherit; }
.check { display: flex; align-items: center; gap: 10px; font-size: .95rem; }
.check input { width: 18px; height: 18px; margin: 0; }
button { width: 100%; font-size: 18px; font-weight: 600; padding: 14px; border: 0; border-radius: 12px;
         background: #2563eb; color: white; cursor: pointer; }
button:disabled { opacity: .5; }
button.quiet { background: transparent; color: inherit; border: 1px solid rgba(127,127,127,.4); font-weight: 500; }
.error { background: #fee2e2; color: #991b1b; border-radius: 10px; padding: 12px 14px; margin: 0 0 16px; }
@media (prefers-color-scheme: dark) { .error { background: #451a1a; color: #fecaca; } }
.ok { background: #dcfce7; color: #166534; border-radius: 10px; padding: 12px 14px; margin: 0 0 16px; }
@media (prefers-color-scheme: dark) { .ok { background: #14532d; color: #bbf7d0; } }
.led { display: inline-block; width: 12px; height: 12px; border-radius: 50%; vertical-align: -1px;
       margin-right: 6px; }
.blue { background: #3b82f6; } .red { background: #ef4444; } .green { background: #22c55e; }
ol { padding-left: 22px; } li { margin: 0 0 10px; }
code { font-size: .95em; background: rgba(127,127,127,.15); padding: 1px 6px; border-radius: 6px; }
.hidden { display: none; }
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _bars(signal: int) -> str:
    level = min(4, max(1, (signal + 24) // 25))
    return "".join("▂▄▆█"[: level]) + "<span style='opacity:.3'>" + "▂▄▆█"[level:] + "</span>"


def _network_row(network: Network, checked: bool) -> str:
    lock = " 🔒" if network.secured else ""
    return (
        "<label class='net'>"
        f"<input type='radio' name='network' value='{escape(network.ssid, quote=True)}'"
        f" data-secured='{int(network.secured)}'{' checked' if checked else ''}>"
        f"<span class='name'>{escape(network.ssid)}{lock}</span>"
        f"<span class='meta'>{_bars(network.signal)}</span></label>"
    )


def wifi_form(networks: list[Network], error: str | None, selected: str | None = None) -> str:
    rows = "".join(_network_row(n, n.ssid == selected) for n in networks)
    if not networks:
        rows = "<p style='padding:12px 16px;margin:0'>No networks found.</p>"
    other_checked = " checked" if selected == "__other__" else ""
    rows += (
        "<label class='net'>"
        f"<input type='radio' name='network' value='__other__' data-secured='1'{other_checked}>"
        "<span class='name'>Other network…</span></label>"
    )
    error_html = f"<div class='error'>{escape(error)}</div>" if error else ""
    body = f"""
<h1>Connect huckdeck to Wi‑Fi</h1>
<p class='sub'>Pick your home network. 2.4GHz networks only — the deck can't see 5GHz.</p>
{error_html}
<form method='post' action='/wifi' autocomplete='off'>
  <div class='card'>{rows}</div>
  <div class='field hidden' id='other'>
    <label for='other_ssid'>Network name</label>
    <input type='text' id='other_ssid' name='other_ssid' maxlength='32' autocapitalize='none'>
    <p class='check' style='margin-top:10px'><input type='checkbox' id='hidden' name='hidden' value='1'>
      <label for='hidden' style='margin:0;opacity:1'>This network is hidden</label></p>
  </div>
  <div class='field hidden' id='pw'>
    <label for='password'>Password</label>
    <input type='password' id='password' name='password' maxlength='64' autocapitalize='none'>
    <p class='check' style='margin-top:10px'><input type='checkbox' id='show'>
      <label for='show' style='margin:0;opacity:1'>Show password</label></p>
  </div>
  <button type='submit' id='go' disabled>Join</button>
</form>
<script>
(function () {{
  var radios = document.querySelectorAll('input[name=network]');
  var other = document.getElementById('other'), pw = document.getElementById('pw');
  var go = document.getElementById('go'), pwInput = document.getElementById('password');
  function update() {{
    var sel = document.querySelector('input[name=network]:checked');
    go.disabled = !sel;
    if (!sel) return;
    var isOther = sel.value === '__other__';
    other.className = isOther ? 'field' : 'field hidden';
    pw.className = sel.getAttribute('data-secured') === '1' ? 'field' : 'field hidden';
    if (isOther) document.getElementById('other_ssid').focus(); else if (sel.getAttribute('data-secured') === '1') pwInput.focus();
  }}
  for (var i = 0; i < radios.length; i++) radios[i].addEventListener('change', update);
  document.getElementById('show').addEventListener('change', function (e) {{
    pwInput.type = e.target.checked ? 'text' : 'password';
  }});
  update();
}})();
</script>
"""
    return _page("huckdeck setup", body)


def joining(ssid: str, hotspot_ssid: str) -> str:
    body = f"""
<h1>Joining {escape(ssid)}…</h1>
<p class='sub'>The deck is switching networks now, so this page will stop responding. Watch the light on the deck.</p>
<div class='card' style='padding:12px 16px'>
  <p><span class='led blue'></span><b>Blinking blue</b> — joining, give it up to a minute.</p>
  <p><span class='led blue'></span><b>Solid blue</b> — connected. Put your phone back on <b>{escape(ssid)}</b>
     and open <code>http://huckdeck.local</code> to finish setup.</p>
  <p style='margin-bottom:0'><span class='led red'></span><b>Blinking red</b> — it couldn't join.
     Reconnect your phone to <b>{escape(hotspot_ssid)}</b> and try again; the error will be shown.</p>
</div>
"""
    return _page("Joining Wi-Fi", body)


def login_form(error: str | None, email: str = "") -> str:
    error_html = f"<div class='error'>{escape(error)}</div>" if error else ""
    body = f"""
<h1>Sign in to Huckleberry</h1>
<p class='sub'>The deck logs button presses to your Huckleberry account. Your password is checked once and not kept on the deck.</p>
{error_html}
<form method='post' action='/login'>
  <div class='field'>
    <label for='email'>Email</label>
    <input type='text' id='email' name='email' inputmode='email' autocapitalize='none' autocomplete='username'
           value='{escape(email, quote=True)}'>
  </div>
  <div class='field'>
    <label for='password'>Password</label>
    <input type='password' id='password' name='password' autocomplete='current-password'>
    <p class='check' style='margin-top:10px'><input type='checkbox' id='show'>
      <label for='show' style='margin:0;opacity:1'>Show password</label></p>
  </div>
  <input type='hidden' id='timezone' name='timezone' value=''>
  <button type='submit' id='go'>Sign in</button>
</form>
<p class='sub' style='margin-top:20px;font-size:.9rem'>Signed up with Apple or Google? The deck can only use an email
and password login. Set a password in the Huckleberry app first (Settings → Account).</p>
<script>
(function () {{
  try {{ document.getElementById('timezone').value = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; }} catch (e) {{}}
  var pw = document.getElementById('password');
  document.getElementById('show').addEventListener('change', function (e) {{ pw.type = e.target.checked ? 'text' : 'password'; }});
  document.querySelector('form').addEventListener('submit', function () {{
    var b = document.getElementById('go'); b.disabled = true; b.textContent = 'Signing in…';
  }});
}})();
</script>
"""
    return _page("Sign in to Huckleberry", body)


def child_picker(children: list[Child], error: str | None) -> str:
    rows = "".join(
        f"<label class='net'><input type='radio' name='child' value='{escape(c.cid, quote=True)}'>"
        f"<span class='name'>{escape(c.name)}</span></label>"
        for c in children
    )
    error_html = f"<div class='error'>{escape(error)}</div>" if error else ""
    body = f"""
<h1>Which child?</h1>
<p class='sub'>This deck logs everything for one child.</p>
{error_html}
<form method='post' action='/child'>
  <div class='card'>{rows}</div>
  <button type='submit' id='go' disabled>Use this child</button>
</form>
<script>
(function () {{
  var radios = document.querySelectorAll('input[name=child]');
  for (var i = 0; i < radios.length; i++) radios[i].addEventListener('change', function () {{
    document.getElementById('go').disabled = false;
  }});
}})();
</script>
"""
    return _page("Which child?", body)


def starting(ssid: str | None, child_name: str | None) -> str:
    what = f"Starting up for <b>{escape(child_name)}</b>…" if child_name else "Starting up…"
    net = f"<div class='ok'>Connected to <b>{escape(ssid)}</b></div>" if ssid else ""
    body = f"<h1>huckdeck</h1>{net}<p>{what} Reload in a few seconds.</p>"
    return _page("huckdeck", body)


def running(child_name: str, ssid: str | None, can_sign_out: bool = True) -> str:
    net = f" on <b>{escape(ssid)}</b>" if ssid else ""
    signout = (
        """<form method='post' action='/signout' onsubmit="return confirm('Sign out of Huckleberry on the deck? You will need to sign in again.')">
  <button type='submit' class='quiet'>Sign out of Huckleberry</button>
</form>"""
        if can_sign_out
        else "<p class='sub' style='font-size:.9rem'>Signed in from the deck's <code>.env</code> file.</p>"
    )
    body = f"""
<h1>huckdeck</h1>
<div class='ok'>Running{net}. Logging events for <b>{escape(child_name)}</b>.</div>
<p>Press a button on the deck: the light double-blinks in that button's colour and the event appears in the Huckleberry app.</p>
{signout}
"""
    return _page("huckdeck", body)


def signed_out() -> str:
    body = "<h1>Signed out</h1><p>The deck is restarting. Reload this page in a few seconds to sign in again.</p>"
    return _page("Signed out", body)
