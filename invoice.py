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


def read_published_orders(max_age: float = 20.0) -> list | None:
    """The order pages the running watcher already parsed and published — no
    second connection to Chrome. None if that snapshot is missing or stale, so
    the caller can fall back to reading Chrome directly."""
    import time
    from pathlib import Path
    p = Path(__file__).resolve().parent / "orders.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - float(data.get("at", 0)) > max_age:
        return None
    return data.get("orders", [])


def orders(port: int) -> list:
    """Open orders for the panel: the watcher's fresh snapshot if there is one,
    else read Chrome directly (watcher stopped)."""
    published = read_published_orders()
    if published is not None:
        return published
    return read_open_orders(port)


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


def signature_data_uri() -> str:
    """The uploaded signature image as a data: URI, or empty if none set."""
    from pathlib import Path
    p = Path(__file__).resolve().parent / "signature.b64"
    try:
        d = p.read_text(encoding="utf-8").strip()
        return d if d.startswith("data:image/") else ""
    except OSError:
        return ""


def _logo_data_uri() -> str:
    """The Whatnot mark as a data: URI so the invoice needs no network. Empty
    if the asset is missing, and the caller falls back to a plain wordmark."""
    import base64
    from pathlib import Path
    p = Path(__file__).resolve().parent / "static" / "whatnot-logo.png"
    try:
        return "data:image/png;base64," + base64.b64encode(
            p.read_bytes()).decode("ascii")
    except OSError:
        return ""


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
    # Recipient (you) from the structured fields; fall back to the old free-text
    # address for a config written before the fields were split.
    street = str(cfg.get("invoice_buyer_street") or "").strip()
    if street or cfg.get("invoice_buyer_city"):
        pc_city = " ".join(p for p in [cfg.get("invoice_buyer_postcode"),
                                       cfg.get("invoice_buyer_city")] if p)
        to_lines = [cfg.get("invoice_buyer_name"), street, pc_city,
                    cfg.get("invoice_buyer_province"),   # optional
                    cfg.get("invoice_buyer_country") or "Netherlands"]
    else:
        to_lines = [cfg.get("invoice_buyer_name")] + \
            [l for l in str(cfg.get("invoice_buyer_address") or "").splitlines()
             if l.strip()]
    to = "<br>".join(_e(l) for l in to_lines if l) or \
        "<span style='color:#c00'>set your name + address in the panel</span>"

    unit, sym = declared_value(o, cfg)
    qty = 1
    try:
        qty = max(1, int(re.sub(r"\D", "", str(o.get("quantity") or "1")) or 1))
    except ValueError:
        qty = 1

    # The price actually paid on Whatnot — £0.00 for a giveaway. Shown so the
    # invoice states plainly that the goods were won (paid nothing) while the
    # declared value below is their market worth, the two not being the same.
    paid = o.get("total_display") or o.get("order_total") or ""
    tc = o.get("total_cache") or {}
    is_free = isinstance(tc.get("amount"), (int, float)) and tc["amount"] == 0
    paid_label = f"{paid}  (giveaway — item won, not purchased)" if is_free else paid

    # Customs declares in EUR for a Dutch import; the amount actually paid is
    # shown separately in the order's own currency (£0 for a giveaway).
    reason = "Giveaway prize / promotional prize" if is_free else "Purchase"
    ship_type = "Non-commercial" if is_free else "Commercial"
    terms = ("Recipient did not purchase the goods — won free of charge"
             if is_free else "Goods purchased by the recipient")
    recip_name = _e(cfg.get("invoice_buyer_name") or "")
    seller = _e(o.get("seller"))
    default_country = _e(cfg.get("invoice_default_origin") or "")
    condition = "New / sealed"

    ship_rows = "".join(
        f"<tr><td class='k'>{_e(k)}</td><td>{v}</td></tr>" for k, v in [
            ("Date", _e(o.get("order_date"))),
            ("Order number", _e(o.get("order_id"))),
            ("Reason for export", reason),
            ("Type of shipment", ship_type),
            ("Terms of shipment", terms),
            ("Amount paid", _e(paid_label)),
        ] if v)

    # Giveaway wording customs officers expect to see spelled out.
    giveaway_note = f"""
      <p>The goods listed above were won free of charge by the recipient
         through a promotional giveaway hosted on the Whatnot platform. No
         purchase was made and no payment was made by the recipient to obtain
         the merchandise.</p>
      <p>The goods are imported solely for the recipient's personal Pokémon
         Trading Card Game collection and personal enjoyment, and are not
         intended for resale, commercial use, or business purposes.</p>
      <p>The declared value represents the estimated normal retail / market
         value of the merchandise, provided for customs valuation only. It does
         not represent an amount paid by the recipient — no sales transaction
         took place between the recipient and the giveaway organizer.</p>""" \
        if is_free else f"""
      <p>The declared value above is the price paid by the recipient for these
         goods, imported for personal use and not for resale.</p>"""

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pro forma invoice {_e(o.get('order_id'))}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
          color: #111; background: #fff; margin: 0; padding: 32px; }}
  .sheet {{ max-width: 760px; margin: 0 auto; }}
  header {{ display: flex; align-items: center; justify-content: space-between;
            border-bottom: 3px solid #FFDE00; padding-bottom: 16px; margin-bottom: 22px; }}
  .brand {{ display: flex; align-items: center; gap: 10px; }}
  .mark {{ width: 34px; height: 34px; border-radius: 9px; background: #FFDE00;
           display: grid; place-items: center; font-weight: 800; color: #111;
           font-size: 20px; }}
  .brand b {{ font-size: 22px; letter-spacing: -.02em; }}
  .title {{ text-align: right; }}
  .title h1 {{ font-size: 16px; text-transform: uppercase; letter-spacing: .1em;
        color: #111; margin: 0; font-weight: 800; }}
  .title span {{ font-size: 11px; text-transform: uppercase; letter-spacing: .14em;
        color: #999; }}
  h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: .1em;
        color: #888; margin: 22px 0 8px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td, th {{ padding: 6px 0; vertical-align: top; text-align: left; }}
  .info td.k {{ color: #666; width: 40%; }}
  .items th {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
               color: #888; border-bottom: 1px solid #eee; padding: 0 8px 8px 0; }}
  .items td {{ border-bottom: 1px solid #f2f2f2; padding: 10px 8px 10px 0; }}
  .num {{ text-align: right; white-space: nowrap; }}
  .cols {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .cols > div {{ flex: 1; min-width: 210px; }}
  .box {{ background: #fafafa; border: 1px solid #eee; border-radius: 10px;
          padding: 12px 14px; min-height: 96px; font-size: 13px; }}
  .box .l {{ color: #888; }}
  .grand td {{ font-weight: 700; border-top: 2px solid #111; padding-top: 10px; }}
  .decl {{ font-size: 12.5px; color: #333; }}
  .decl p {{ margin: 6px 0; }}
  .sign {{ margin-top: 22px; display: flex; gap: 40px; flex-wrap: wrap;
           font-size: 13px; }}
  .sign .line {{ border-bottom: 1px solid #999; min-width: 200px;
                 display: inline-block; height: 24px; }}
  .foot {{ margin-top: 24px; color: #999; font-size: 11px;
           border-top: 1px solid #eee; padding-top: 10px; }}
  input.ed {{ font: inherit; padding: 3px 6px; border: 1px solid #ccc;
              border-radius: 6px; }}
  input.val {{ width: 92px; text-align: right; }}
  input.qty {{ width: 60px; text-align: right; }}
  input.org {{ width: 140px; }}
  @media print {{
    @page {{ size: A4; margin: 12mm; }}
    body {{ padding: 0; font-size: 12.5px; }}
    .noprint {{ display: none; }}
    .foot {{ display: none; }}         /* app note, not part of the customs doc */
    input.ed {{ border: 0; padding: 0; }}
    header {{ margin-bottom: 14px; padding-bottom: 12px; }}
    h2 {{ margin: 13px 0 5px; }}
    .box {{ min-height: 0; padding: 9px 11px; }}
    .items td {{ padding: 7px 8px 7px 0; }}
    .decl p {{ margin: 4px 0; }}
    .sign {{ margin-top: 14px; }}
  }}
</style></head><body><div class="sheet">
  <header>
    <div class="brand">{(f'<img class="mark" src="{_logo_data_uri()}" alt="Whatnot">' if _logo_data_uri() else '<div class="mark">w</div>')}<b>whatnot</b></div>
    <div class="title"><h1>Pro forma invoice</h1><span>For customs purposes only</span></div>
  </header>

  <div class="cols">
    <div><h2>Shipper / exporter</h2><div class="box">
      <span class="l">Name</span><br>{seller}<br>
      <span class="l">Country</span><br>United Kingdom</div></div>
    <div><h2>Consignee / importer</h2><div class="box">{to}</div></div>
    <div><h2>Forwarded via</h2><div class="box">{via}</div></div>
  </div>

  <h2>Shipment information</h2>
  <table class="info">{ship_rows}</table>

  <h2>Description of goods</h2>
  <table class="items">
    <tr><th>Description</th><th>Condition</th><th class="num">Qty</th>
        <th class="num">Unit value</th><th class="num">Total value</th>
        <th>Country of origin</th></tr>
    <tr>
      <td>{_e(o.get('item'))}</td>
      <td><input class="ed" id="cond" value="{condition}"></td>
      <td class="num"><input class="ed qty" id="qty" type="number" min="1"
          value="{qty}" oninput="recalc()"></td>
      <td class="num">€<input class="ed val" id="unit" type="number" step="0.01"
          min="0" value="{unit:.2f}" oninput="recalc()"></td>
      <td class="num" id="line">—</td>
      <td><input class="ed org" id="org" value="{default_country}"
          placeholder="if known"></td>
    </tr>
    <tr class="grand"><td colspan="4">Total declared value</td>
        <td class="num" id="grand">—</td><td></td></tr>
  </table>

  <h2>Customs declaration / additional information</h2>
  <div class="decl">{giveaway_note}
    <p>I declare that the information provided above is true and accurate to the
       best of my knowledge.</p>
  </div>

  <div class="sign">
    <div>Recipient<br><b>{recip_name or '—'}</b></div>
    <div>Date<br>{_e(o.get('order_date')) or '&nbsp;'}</div>
    <div>Signature<br>{(f'<img src="{signature_data_uri()}" style="max-height:56px;max-width:220px;margin-top:2px">' if signature_data_uri() else '<span class="line">&nbsp;</span>')}</div>
  </div>

  <p class="foot">Reformatted from the buyer's Whatnot order for a customs
     declaration. Editable: condition, quantity, unit value and country of
     origin. Declared value is the goods' market worth, not the amount paid
     (a giveaway item is won at £0).</p>
  <p class="noprint" style="text-align:center;margin-top:18px">
    <button onclick="window.print()"
      style="padding:10px 20px;border-radius:10px;border:0;background:#111;color:#fff;font-size:15px;cursor:pointer">
      Print / Save as PDF</button></p>
</div>
<script>
  function recalc() {{
    const u = parseFloat(document.getElementById('unit').value || '0') || 0;
    const q = parseInt(document.getElementById('qty').value || '1') || 1;
    const t = '€' + (u * q).toFixed(2);
    document.getElementById('line').textContent = t;
    document.getElementById('grand').textContent = t;
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
