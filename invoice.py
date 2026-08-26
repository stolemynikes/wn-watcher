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


def invoice_html(o: dict) -> str:
    """A print-ready customs invoice for one order. Whatnot wordmark up top,
    the order's own values below — the same receipt, laid out to submit."""
    ship = "<br>".join(_e(l) for l in o.get("ship_lines", [])) or "—"
    rows = [
        ("Order number", o.get("order_id")),
        ("Order date", o.get("order_date")),
        ("Seller", o.get("seller")),
        ("Category", o.get("category")),
        ("Status", o.get("status")),
        ("Tracking number", o.get("tracking")),
    ]
    info = "".join(
        f"<tr><td class='k'>{_e(k)}</td><td>{_e(v)}</td></tr>"
        for k, v in rows if v)
    total = _e(o.get("total_display") or o.get("order_total"))
    qty = _e(o.get("quantity") or "1")
    item = _e(o.get("item"))
    line_rows = "".join([
        f"<tr><td>{item}</td><td class='num'>{qty}</td>"
        f"<td class='num'>{total}</td></tr>",
    ])
    money = lambda v: f"<tr><td class='k'>{_e(v[0])}</td>" \
                      f"<td class='num'>{_e(v[1])}</td></tr>"
    receipt = "".join(money(r) for r in [
        ("Subtotal", o.get("subtotal")), ("Shipping", o.get("shipping")),
        ("Taxes", o.get("taxes")), ("Order total", o.get("total_display")
                                    or o.get("order_total")),
    ] if r[1])

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
        color: #888; margin: 24px 0 8px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 7px 0; vertical-align: top; }}
  .info td.k, .receipt td.k {{ color: #666; width: 42%; }}
  .items th {{ text-align: left; font-size: 12px; text-transform: uppercase;
               letter-spacing: .08em; color: #888; border-bottom: 1px solid #eee;
               padding: 0 0 8px; }}
  .items td {{ border-bottom: 1px solid #f2f2f2; padding: 12px 0; }}
  .num {{ text-align: right; white-space: nowrap; }}
  .cols {{ display: flex; gap: 40px; flex-wrap: wrap; }}
  .cols > div {{ flex: 1; min-width: 220px; }}
  .ship {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px;
           padding: 12px 14px; }}
  .receipt tr:last-child td {{ font-weight: 700; border-top: 2px solid #111;
                               padding-top: 10px; }}
  .foot {{ margin-top: 28px; color: #999; font-size: 12px;
           border-top: 1px solid #eee; padding-top: 12px; }}
  @media print {{ body {{ padding: 0; }} .noprint {{ display: none; }} }}
</style></head><body><div class="sheet">
  <header>
    <div class="brand"><div class="mark">w</div><b>whatnot</b></div>
    <h1>Order receipt / Customs invoice</h1>
  </header>

  <div class="cols">
    <div>
      <h2>Order</h2>
      <table class="info">{info}</table>
    </div>
    <div>
      <h2>Ship to</h2>
      <div class="ship">{ship}</div>
    </div>
  </div>

  <h2>Item</h2>
  <table class="items">
    <tr><th>Description</th><th class="num">Qty</th><th class="num">Value</th></tr>
    {line_rows}
  </table>

  <h2>Receipt</h2>
  <table class="receipt">{receipt}</table>

  <p class="foot">Generated from the Whatnot order above for a customs
     declaration. Values are as shown on the order; a giveaway item is £0.00.</p>
  <p class="noprint" style="text-align:center;margin-top:20px">
    <button onclick="window.print()"
      style="padding:10px 20px;border-radius:10px;border:0;background:#111;color:#fff;font-size:15px;cursor:pointer">
      Print / Save as PDF</button></p>
</div></body></html>"""


if __name__ == "__main__":
    import chrome
    orders = read_open_orders(int(chrome.load_config().get("debug_port", 9222)))
    print(json.dumps([{k: v for k, v in o.items()
                       if k not in ("ship_to", "total_cache")}
                      for o in orders], indent=2, ensure_ascii=False))
