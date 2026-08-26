#!/usr/bin/env python3
"""Turn a Whatnot Order Details page into a clean customs invoice.

You forward parcels through a service (myUS and the like) that wants an invoice
per parcel for the customs declaration. Whatnot has no "download invoice"
button, so the manual way is to screenshot the order receipt. This reads the
order you already have open — the same page that shows the tracking number —
and lays its real values out as a printable invoice.

Nothing is invented: item, order number, seller, date, the receipt totals, the
ship-to address and the tracking number all come straight off that page (a
giveaway win really is £0.00, a paid order shows its price). It only reformats
a genuine order you would otherwise screenshot.
"""

import html
import json
import re

# Read both the rendered text (item, order id, values, tracking) and the page's
# own Apollo cache (the ship-to address, which carries the recipient name the
# visible text omits) in one pass, so an order needs a single evaluate.
ORDER_READ_JS = r"""
() => {
    const text = (document.body && document.body.innerText || "");
    if (!/Order details/i.test(text)) return {ok: false};

    // label -> the line right after it, for the plainly-labelled fields.
    const lines = text.split("\n").map(s => s.trim());
    const after = (label) => {
        for (let i = 0; i < lines.length - 1; i++) {
            if (lines[i].toLowerCase() === label.toLowerCase()) return lines[i + 1];
        }
        return "";
    };
    // The item title is the first line after the "Order details" heading.
    let item = "";
    const h = lines.findIndex(l => /^order details$/i.test(l));
    if (h >= 0 && lines[h + 1]) item = lines[h + 1];

    // Receipt block: the second occurrence of these labels is the money.
    const moneyAfter = (label) => {
        const hits = [];
        for (let i = 0; i < lines.length - 1; i++)
            if (lines[i].toLowerCase() === label.toLowerCase()) hits.push(lines[i + 1]);
        return hits.length ? hits[hits.length - 1] : "";
    };

    // Ship-to with the recipient's name, from the cache (the page hides it).
    let ship = null, total = null, tracking = after("Track your purchase");
    const c = window.__APOLLO_CLIENT__;
    if (c) {
        const d = c.cache.extract();
        for (const v of Object.values(d)) {
            if (!v || typeof v !== "object") continue;
            if (v.__typename === "AddressNode" && !ship) {
                ship = {name: v.fullName || "", line1: v.line1 || "",
                        line2: v.line2 || "", city: v.city || "",
                        state: v.state || "", postalCode: v.postalCode || "",
                        countryCode: v.countryCode || ""};
            }
            if (v.__typename === "OrderNode" && v.total && !total) {
                total = {amount: v.total.amount, currency: v.total.currency};
            }
        }
    }
    return {
        ok: true,
        url: location.href,
        item: item,
        order_id: after("Order ID"),
        order_date: after("Order date"),
        seller: after("Sold By"),
        quantity: after("Quantity"),
        category: after("Category"),
        subtotal: moneyAfter("Subtotal"),
        shipping: moneyAfter("Shipping"),
        taxes: moneyAfter("Taxes"),
        order_total: moneyAfter("Order Total"),
        tracking: tracking,
        status: (text.match(/Order has arrived|Preparing Package|Shipped|Delivered|Completed/i) || [""])[0],
        delivered: (text.match(/Your item was delivered on [^\n]+/i) || [""])[0],
        total_cache: total,
        ship_to: ship,
    };
}
"""


def _clean(order: dict) -> dict:
    """Fill the ship-to and total from the cache when present, and normalise."""
    o = dict(order)
    ship = o.get("ship_to") or {}
    o["ship_lines"] = [x for x in [
        ship.get("name"), ship.get("line1"), ship.get("line2"),
        " ".join(p for p in [ship.get("postalCode"), ship.get("city")] if p),
        ship.get("countryCode"),
    ] if x]
    # Prefer the cache's exact amount + currency for the headline value.
    tc = o.get("total_cache")
    if tc and tc.get("currency"):
        cents = tc.get("amount")
        amt = (cents / 100) if isinstance(cents, (int, float)) else 0
        sym = {"GBP": "£", "EUR": "€", "USD": "$"}.get(tc["currency"], tc["currency"] + " ")
        o["total_display"] = f"{sym}{amt:,.2f}"
        o["currency"] = tc["currency"]
    else:
        o["total_display"] = o.get("order_total") or ""
        o["currency"] = ""
    return o


def read_open_orders(port: int) -> list:
    """Every Whatnot Order Details page open in the browser, parsed. Uses
    Playwright over CDP; any tab that will not read is skipped."""
    from playwright.sync_api import sync_playwright
    out = []
    try:
        pw = sync_playwright().start()
    except Exception:
        return out
    try:
        browser = pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=10000)
    except Exception:
        pw.stop()
        return out
    try:
        for ctx in browser.contexts:
            for page in ctx.pages:
                if "/order/" not in (page.url or ""):
                    continue
                try:
                    data = page.evaluate(ORDER_READ_JS)
                except Exception:
                    continue
                if isinstance(data, dict) and data.get("ok"):
                    out.append(_clean(data))
    finally:
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()
    return out


def _e(s) -> str:
    return html.escape(str(s or ""))


_SYM = {"GBP": "£", "EUR": "€", "USD": "$"}


def declared_value(o: dict, cfg: dict) -> tuple:
    """(unit value as a number, currency symbol). A paid order declares its own
    price; a giveaway (order total 0) declares the configured default, since a
    free item still has a real worth the customs form wants — never £0."""
    tc = o.get("total_cache") or {}
    cents = tc.get("amount")
    currency = tc.get("currency") or "EUR"
    sym = _SYM.get(currency, currency + " ")
    if isinstance(cents, (int, float)) and cents > 0:
        return round(cents / 100, 2), sym
    # Giveaway / £0 — the operator's default declared value.
    try:
        return round(float(cfg.get("invoice_giveaway_value") or 0), 2), sym
    except (TypeError, ValueError):
        return 0.0, sym


def invoice_html(o: dict, cfg: dict | None = None) -> str:
    """A print-ready commercial/customs invoice: seller it came FROM, you it
    goes TO (your own address, from config), the forwarder it went VIA (the
    myUS-style address on the order), and an editable declared value so a
    giveaway win carries its real worth, not £0."""
    cfg = cfg or {}
    via = "<br>".join(_e(l) for l in o.get("ship_lines", [])) or "—"
    buyer_name = cfg.get("invoice_buyer_name") or ""
    buyer_addr = str(cfg.get("invoice_buyer_address") or "")
    to_lines = [buyer_name] + [l for l in buyer_addr.splitlines() if l.strip()]
    to = "<br>".join(_e(l) for l in to_lines if l) or \
        "<span style='color:#c00'>set your name + address in the panel</span>"

    unit, sym = declared_value(o, cfg)
    qty = 1
    try:
        qty = max(1, int(re.sub(r"\D", "", str(o.get("quantity") or "1")) or 1))
    except ValueError:
        qty = 1

    info = "".join(
        f"<tr><td class='k'>{_e(k)}</td><td>{_e(v)}</td></tr>"
        for k, v in [
            ("Order number", o.get("order_id")),
            ("Order date", o.get("order_date")),
            ("Category", o.get("category")),
            ("Status", o.get("status")),
            ("Tracking number", o.get("tracking")),
        ] if v)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Invoice {_e(o.get('order_id'))}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
          color: #111; background: #fff; margin: 0; padding: 32px; }}
  .sheet {{ max-width: 720px; margin: 0 auto; }}
  header {{ display: flex; align-items: center; justify-content: space-between;
            border-bottom: 3px solid #FFDE00; padding-bottom: 16px; margin-bottom: 24px; }}
  .brand {{ display: flex; align-items: center; gap: 10px; }}
  .mark {{ width: 34px; height: 34px; border-radius: 9px; background: #FFDE00;
           display: grid; place-items: center; font-weight: 800; color: #111;
           font-size: 20px; }}
  .brand b {{ font-size: 22px; letter-spacing: -.02em; }}
  h1 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .12em;
        color: #666; margin: 0; text-align: right; font-weight: 700; }}
  h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .1em;
        color: #888; margin: 22px 0 8px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 7px 0; vertical-align: top; }}
  .info td.k {{ color: #666; width: 42%; }}
  .items th {{ text-align: left; font-size: 12px; text-transform: uppercase;
               letter-spacing: .08em; color: #888; border-bottom: 1px solid #eee;
               padding: 0 0 8px; }}
  .items td {{ border-bottom: 1px solid #f2f2f2; padding: 12px 0; }}
  .num {{ text-align: right; white-space: nowrap; }}
  .cols {{ display: flex; gap: 32px; flex-wrap: wrap; }}
  .cols > div {{ flex: 1; min-width: 200px; }}
  .box {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px;
          padding: 12px 14px; min-height: 62px; }}
  .grand {{ font-weight: 700; border-top: 2px solid #111; }}
  .foot {{ margin-top: 26px; color: #999; font-size: 12px;
           border-top: 1px solid #eee; padding-top: 12px; }}
  #unit {{ width: 100px; text-align: right; font: inherit; padding: 4px 6px;
           border: 1px solid #ccc; border-radius: 6px; }}
  @media print {{ body {{ padding: 0; }} .noprint {{ display: none; }}
                  #unit {{ border: 0; padding: 0; }} }}
</style></head><body><div class="sheet">
  <header>
    <div class="brand"><div class="mark">w</div><b>whatnot</b></div>
    <h1>Commercial invoice / Customs declaration</h1>
  </header>

  <div class="cols">
    <div><h2>From (seller)</h2><div class="box">{_e(o.get('seller'))}<br>United Kingdom</div></div>
    <div><h2>To (recipient)</h2><div class="box">{to}</div></div>
    <div><h2>Forwarded via</h2><div class="box">{via}</div></div>
  </div>

  <h2>Order</h2>
  <table class="info">{info}</table>

  <h2>Goods</h2>
  <table class="items">
    <tr><th>Description</th><th class="num">Qty</th>
        <th class="num">Unit value</th><th class="num">Amount</th></tr>
    <tr>
      <td>{_e(o.get('item'))}</td>
      <td class="num">{qty}</td>
      <td class="num">{sym}<input id="unit" type="number" step="0.01"
          min="0" value="{unit:.2f}" oninput="recalc()"></td>
      <td class="num" id="line">—</td>
    </tr>
    <tr class="grand"><td colspan="3">Total declared value</td>
        <td class="num" id="grand">—</td></tr>
  </table>

  <p class="foot">Reformatted from the buyer's Whatnot order for a customs
     declaration. The declared value is editable: a paid order carries its
     price; a free/giveaway item is declared at its real market value, which
     you set — customs asks for the goods' worth, not what was paid.</p>
  <p class="noprint" style="text-align:center;margin-top:18px">
    <button onclick="window.print()"
      style="padding:10px 20px;border-radius:10px;border:0;background:#111;color:#fff;font-size:15px;cursor:pointer">
      Print / Save as PDF</button></p>
</div>
<script>
  const SYM = {json.dumps(sym)}, QTY = {qty};
  function recalc() {{
    const u = parseFloat(document.getElementById('unit').value || '0') || 0;
    const t = (u * QTY).toFixed(2);
    document.getElementById('line').textContent = SYM + t;
    document.getElementById('grand').textContent = SYM + t;
  }}
  recalc();
</script>
</body></html>"""


if __name__ == "__main__":
    import chrome
    orders = read_open_orders(int(chrome.load_config().get("debug_port", 9222)))
    print(json.dumps([{k: v for k, v in o.items()
                       if k not in ("ship_to", "total_cache")}
                      for o in orders], indent=2, ensure_ascii=False))
