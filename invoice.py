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

import functools
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


@functools.lru_cache(maxsize=1)
def signature_data_uri() -> str:
    """The uploaded signature image as a data: URI, or empty if none set."""
    from pathlib import Path
    p = Path(__file__).resolve().parent / "signature.b64"
    try:
        d = p.read_text(encoding="utf-8").strip()
        return d if d.startswith("data:image/") else ""
    except OSError:
        return ""


@functools.lru_cache(maxsize=1)
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



def fetch_eur_rates() -> dict | None:
    """EUR value of 1 GBP / 1 USD at today's ECB reference rate, so a foreign
    order can be declared in euros at what it is worth that day. Cached to a
    file for the day (a print does not re-hit the network). None on failure,
    and the invoice then just shows the entered currency, unconverted."""
    import time
    import urllib.request
    from pathlib import Path
    cache = Path(__file__).resolve().parent / "fx_rates.json"
    today = time.strftime("%Y-%m-%d")
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("fetched") == today:
            return d
    except (OSError, ValueError):
        pass
    try:
        req = urllib.request.Request(
            "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=GBP,USD",
            headers={"User-Agent": "wn-watcher/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
        r = data["rates"]
        out = {"EUR": 1.0, "GBP": round(1 / r["GBP"], 6),
               "USD": round(1 / r["USD"], 6),
               "date": data.get("date", today), "fetched": today}
        cache.write_text(json.dumps(out), encoding="utf-8")
        return out
    except Exception:
        return None


PURCHASES_URL = "https://www.whatnot.com/?activityTab=purchases"
ORDER_URL = "https://www.whatnot.com/order/{uuid}"


def _cdp(port: int):
    """A Playwright handle on the browser the watcher already drives. Never
    launches anything: if that browser is not there, this raises and the caller
    says so rather than quietly starting a second one."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=15000)
    except Exception:
        pw.stop()
        raise
    return pw, browser


# The same GetMyPurchases query the activity panel already has mounted, walked
# page by page with its own cursor. watch.py asks that query for the newest
# twenty to spot a win; an invoice run reaches further back, so this one keeps
# following pageInfo.endCursor until it has enough rows or the history ends.
MY_PURCHASES_JS = r"""
    async (want) => {
        const client = window.__APOLLO_CLIENT__;
        if (!client) return {error: "no apollo client on this page"};
        let target = null;
        for (const [, q] of client.getObservableQueries()) {
            const name = q.queryName || (q.options && q.options.query &&
                q.options.query.definitions[0].name.value);
            if (name === "GetMyPurchases") { target = q; break; }
        }
        if (!target) return {error: "GetMyPurchases is not mounted on this tab"};
        const rows = [], seen = new Set();
        let cursor = null, pages = 0;
        while (pages < 15 && rows.length < want) {
            const res = await target.refetch({cursor: cursor, first: 20,
                                              status: []});
            const mo = (res.data && res.data.myOrders) || {};
            for (const e of (mo.edges || [])) {
                const n = e.node;
                if (!n || seen.has(n.uuid)) continue;
                seen.add(n.uuid);
                rows.push({
                    uuid: n.uuid,
                    status: n.status,
                    prettyStatus: n.prettyStatus,
                    amount: (n.total && n.total.amount) ?? -1,
                    currency: (n.total && n.total.currency) || "",
                    createdAt: n.createdAt,
                    title: (n.items && n.items.edges[0] &&
                        n.items.edges[0].node.listing &&
                        n.items.edges[0].node.listing.title) || "",
                });
            }
            pages++;
            const pi = mo.pageInfo || {};
            if (!pi.hasNextPage || !pi.endCursor) break;
            cursor = pi.endCursor;
        }
        return {ok: true, rows: rows};
    }
"""


def my_purchases(port: int, want: int = 120) -> list:
    """Your purchase history, newest first, from the page's own GetMyPurchases
    query — the same list the activity panel shows, as structured rows:
    uuid, status, amount, currency, createdAt, title.

    `want` is how far back to go; the query hands out twenty at a time, so
    asking for 120 is six round trips, and a shorter history simply ends.

    That query only exists where the app mounted it, so this uses the purchases
    tab when one is open and otherwise opens one itself and closes it again.
    """
    import time
    pw, browser = _cdp(port)
    opened = None
    try:
        page = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                if "activityTab=purchases" in (p.url or ""):
                    page = p
        if page is None:
            if not browser.contexts:
                return []
            page = opened = browser.contexts[0].new_page()
            page.goto(PURCHASES_URL, wait_until="domcontentloaded",
                      timeout=45000)
        rows, deadline = None, time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                res = page.evaluate(MY_PURCHASES_JS, want)
            except Exception:
                res = None
            if isinstance(res, dict) and res.get("ok"):
                rows = res.get("rows") or []
                break
            time.sleep(2)
        return rows or []
    finally:
        if opened is not None:
            try:
                opened.close()
            except Exception:
                pass
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()


def read_orders_by_uuid(port: int, uuids: list,
                        each_timeout: float = 35.0) -> tuple:
    """The order pages behind these purchase ids, read one after another in a
    single tab of our own, which is closed again afterwards. Returns
    (orders, missed) — the ids whose page never showed its details end up in
    `missed` instead of silently yielding a blank invoice.

    This is the invoice path for orders you never opened by hand: twenty order
    tabs is a lot of clicking, and the details only need to be read once.
    Whatnot renders them client-side, so each page is polled until its own text
    says "Order details" rather than read once on load.
    """
    import time
    orders, missed = [], []
    pw, browser = _cdp(port)
    tab = None
    try:
        if not browser.contexts:
            return orders, list(uuids)
        tab = browser.contexts[0].new_page()
        for uuid in uuids:
            try:
                tab.goto(ORDER_URL.format(uuid=uuid),
                         wait_until="domcontentloaded", timeout=45000)
            except Exception:
                missed.append(uuid)
                continue
            data, deadline = None, time.monotonic() + each_timeout
            while time.monotonic() < deadline:
                try:
                    data = tab.evaluate(ORDER_READ_JS)
                except Exception:
                    data = None
                if isinstance(data, dict) and data.get("ok"):
                    break
                time.sleep(1.5)
            if isinstance(data, dict) and data.get("ok"):
                o = _clean(data)
                o["uuid"] = uuid
                orders.append(o)
            else:
                missed.append(uuid)
    finally:
        if tab is not None:
            try:
                tab.close()
            except Exception:
                pass
        try:
            browser.close()
        except Exception:
            pass
        pw.stop()
    return orders, missed


def _sheet(o: dict, cfg: dict, rates: dict | None) -> str:
    """One invoice, as the <div class="sheet"> that goes inside the document.

    Every field the script touches is found by class within its own sheet, and
    the two numbers it needs — the invoice currency and that day's rate — ride
    along as data attributes, so a document can hold any number of these.
    """
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

    # One currency for the whole invoice, picked in the panel: AUTO follows the
    # order's own currency (pounds for a UK stream, euros for a Dutch one), or
    # a fixed EUR / GBP / USD. The declared value is always entered in euros in
    # the panel — a free giveaway has no price of its own — and converted here
    # at that day's ECB rate, so the invoice reads in a single currency.
    tc = o.get("total_cache") or {}
    cents = tc.get("amount")
    order_cur = (tc.get("currency") or "").upper()
    if order_cur not in _SYM:
        order_cur = "EUR"
    is_paid = isinstance(cents, (int, float)) and cents > 0
    # Base value in euros: a paid order's own price brought to euros, else the
    # configured default value for a giveaway item.
    if is_paid:
        amt = round(cents / 100, 2)
        base_eur = (round(amt * rates[order_cur], 2)
                    if order_cur != "EUR" and rates and rates.get(order_cur)
                    else amt)
    else:
        try:
            base_eur = round(float(cfg.get("invoice_giveaway_value") or 0), 2)
        except (TypeError, ValueError):
            base_eur = 0.0
    choice = (cfg.get("invoice_currency") or "AUTO").upper()
    target = order_cur if choice in ("", "AUTO", "NONE") else choice
    if target not in _SYM:
        target = "EUR"
    # You value goods in euros, so the field you type in stays euros on screen.
    # The printed invoice is the customs document, and that is issued in the
    # invoice currency: printing swaps the euro figures for converted ones and
    # states the total in both, which is what the declaration is read against.
    conv_note = entry_hint = ""
    if target == "EUR" or not (rates and rates.get(target)):
        target, per_eur = "EUR", 0
    else:
        per_eur = round(1 / rates[target], 6)          # target units per 1 EUR
        conv_note = (f"Values converted from euros at that day's ECB rate "
                     f"({rates['date']}): 1 EUR = {per_eur:.4f} {target}.")
        entry_hint = (f"You type euros. Printing / saving as PDF issues the "
                      f"invoice in {target} at that day's ECB rate — the total "
                      f"is stated in both.")
    sym = _SYM[target]
    qty = 1
    try:
        qty = max(1, int(re.sub(r"\D", "", str(o.get("quantity") or "1")) or 1))
    except ValueError:
        qty = 1

    # The price actually paid on Whatnot — £0.00 for a giveaway. Shown so the
    # invoice states plainly that the goods were won (paid nothing) while the
    # declared value below is their market worth, the two not being the same.
    paid = o.get("total_display") or o.get("order_total") or ""
    is_free = isinstance(cents, (int, float)) and cents == 0
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
            ("Tracking number", _e(o.get("tracking"))),
            ("Reason for export", reason),
            ("Type of shipment", ship_type),
            ("Terms of shipment", terms),
            ("Amount paid", _e(paid_label)),
        ] if v)

    # Giveaway wording customs officers expect to see spelled out.
    giveaway_note = """
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
        if is_free else """
      <p>The declared value above is the price paid by the recipient for these
         goods, imported for personal use and not for resale.</p>"""

    return f"""<div class="sheet" data-sym="{_e(sym)}" data-per-eur="{per_eur}">
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
    <colgroup><col style="width:33%"><col style="width:14%"><col style="width:6%">
      <col style="width:12%"><col style="width:12%"><col style="width:23%"></colgroup>
    <tr><th>Description</th><th>Condition</th><th class="num">Qty</th>
        <th class="num">Unit value</th><th class="num">Total value</th>
        <th>Country of origin</th></tr>
    <tr>
      <td>{_e(o.get('item'))}</td>
      <td><input class="ed cond" value="{condition}"></td>
      <td class="num"><input class="ed qty" type="number" min="1"
          value="{qty}"></td>
      <td class="num"><span class="scr"><span class="sym">€</span><input
          class="ed val unit" type="number" step="0.01" min="0"
          value="{base_eur:.2f}"></span><span class="prt unitPrint">—</span></td>
      <td class="num"><span class="scr line">—</span><span
          class="prt linePrint">—</span></td>
      <td><input class="ed org" value="{default_country}"
          placeholder="if known"></td>
    </tr>
    <tr class="grand"><td colspan="4">Total declared value</td>
        <td class="num total">—</td><td></td></tr>
  </table>
  <div class="fxnote">{_e(conv_note)}</div>
  <div class="fxnote noprint hint">{_e(entry_hint)}</div>

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
</div>"""


_STYLE = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
          color: #111; background: #fff; margin: 0; padding: 32px; }
  .sheet { max-width: 760px; margin: 0 auto; }
  .sheet + .sheet { margin-top: 48px; border-top: 1px dashed #ddd;
                    padding-top: 36px; }
  header { display: flex; align-items: center; justify-content: space-between;
            border-bottom: 3px solid #FFDE00; padding-bottom: 16px; margin-bottom: 22px; }
  .brand { display: flex; align-items: center; gap: 10px; }
  .mark { width: 34px; height: 34px; border-radius: 9px; background: #FFDE00;
           display: grid; place-items: center; font-weight: 800; color: #111;
           font-size: 20px; }
  .brand b { font-size: 22px; letter-spacing: -.02em; }
  .title { text-align: right; }
  .title h1 { font-size: 16px; text-transform: uppercase; letter-spacing: .1em;
        color: #111; margin: 0; font-weight: 800; }
  .title span { font-size: 11px; text-transform: uppercase; letter-spacing: .14em;
        color: #999; }
  h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .1em;
        color: #888; margin: 22px 0 8px; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  td, th { padding: 6px 0; vertical-align: top; text-align: left; }
  .info td.k { color: #666; width: 40%; }
  .items th { font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
               color: #888; border-bottom: 1px solid #eee; padding: 0 10px 9px 0; }
  .items td { border-bottom: 1px solid #f2f2f2; padding: 14px 10px 14px 0; }
  .num { text-align: right; white-space: nowrap; }
  .items th:last-child, .items td:last-child { padding-left: 14px; }
  .cols { display: flex; gap: 24px; flex-wrap: wrap; }
  .cols > div { flex: 1; min-width: 210px; }
  .box { background: #fafafa; border: 1px solid #eee; border-radius: 10px;
          padding: 12px 14px; min-height: 96px; font-size: 13px; }
  .box .l { color: #888; }
  .grand td { font-weight: 700; border-top: 2px solid #111;
                padding-top: 13px; padding-bottom: 4px; }
  .decl { font-size: 12.5px; color: #333; }
  .decl p { margin: 6px 0; }
  .sign { margin-top: 22px; display: flex; gap: 40px; flex-wrap: wrap;
           font-size: 13px; }
  .sign .line { border-bottom: 1px solid #999; min-width: 200px;
                 display: inline-block; height: 24px; }
  .foot { margin-top: 24px; color: #999; font-size: 11px;
           border-top: 1px solid #eee; padding-top: 10px; }
  input.ed { font: inherit; padding: 3px 6px; border: 1px solid #ccc;
              border-radius: 6px; }
  input.val { width: 62px; text-align: left; padding-left: 2px; }
  .sym { color: inherit; margin-right: 1px; }
  .prt { display: none; }
  .hint { color: #999; font-style: italic; }
  .fxnote { text-align: right; color: #666; font-size: 11.5px; margin: 7px 2px 0; }
  input.qty { width: 52px; text-align: right; }
  input.org { width: 100%; min-width: 0; }
  input.cond { width: 100%; }
  .bar { max-width: 760px; margin: 0 auto 26px; text-align: center; }
  .bar button { padding: 10px 20px; border-radius: 10px; border: 0;
                background: #111; color: #fff; font-size: 15px; cursor: pointer; }
  .bar .note { color: #888; font-size: 12px; margin-top: 8px; }
  .bar .fill { margin-top: 12px; font-size: 13px; color: #555; }
  .bar .fill input { width: 80px; }
  .bar button.link { background: none; color: #06c; font-size: 13px;
                     padding: 4px 2px; text-decoration: underline; }
  @media print {
    @page { size: A4; margin: 12mm; }
    body { padding: 0; font-size: 12.5px; }
    .noprint { display: none; }
    .foot { display: none; }         /* app note, not part of the customs doc */
    input.ed { border: 0; padding: 0; }
    .scr { display: none; }
    .prt { display: inline; }
    header { margin-bottom: 14px; padding-bottom: 12px; }
    h2 { margin: 13px 0 5px; }
    .box { min-height: 0; padding: 9px 11px; }
    .items td { padding: 11px 10px 11px 0; }
    .decl p { margin: 4px 0; }
    .sign { margin-top: 14px; }
    /* One invoice per sheet of paper. */
    .sheet { break-after: page; page-break-after: always; margin: 0;
             border-top: 0; padding-top: 0; }
    .sheet:last-of-type { break-after: auto; page-break-after: auto; }
  }
"""

# Each sheet carries its own currency and rate, so the maths runs per sheet and
# a document with twenty invoices behaves exactly like one with a single
# invoice. Fields are found by class inside their own sheet, never by id.
_SCRIPT = """
  // One value for the whole batch: fills every sheet and lets each one
  // recalculate itself through its own input handler.
  window.fillAll = function () {
    const box = document.getElementById('fillAll');
    if (!box || box.value === '') return;
    document.querySelectorAll('.sheet input.unit').forEach(el => {
      el.value = box.value;
      el.dispatchEvent(new Event('input'));
    });
  };
  document.querySelectorAll('.sheet').forEach(sheet => {
    const sym = sheet.dataset.sym || '€';
    const perEur = parseFloat(sheet.dataset.perEur || '0') || 0;
    const q = (sel) => sheet.querySelector(sel);
    const set = (sel, text) => { const el = q(sel); if (el) el.textContent = text; };
    function recalc() {
      const u = parseFloat((q('input.unit') || {}).value || '0') || 0;
      const n = parseInt((q('input.qty') || {}).value || '1') || 1;
      const eur = u * n;
      set('.line', '\\u20ac' + eur.toFixed(2));
      if (!perEur) {
        set('.unitPrint', '\\u20ac' + u.toFixed(2));
        set('.linePrint', '\\u20ac' + eur.toFixed(2));
        set('.total', '\\u20ac' + eur.toFixed(2));
        return;
      }
      // Round the unit first, then multiply, so the printed column adds up.
      const unitT = Math.round(u * perEur * 100) / 100;
      const totT = unitT * n;
      set('.unitPrint', sym + unitT.toFixed(2));
      set('.linePrint', sym + totT.toFixed(2));
      set('.total', sym + totT.toFixed(2) + ' / \\u20ac' + eur.toFixed(2));
    }
    sheet.querySelectorAll('input.unit, input.qty').forEach(
      el => el.addEventListener('input', recalc));
    recalc();
  });
"""


def _document(orders: list, cfg: dict | None, title: str, note: str = "") -> str:
    """The printable page: one shared stylesheet, one print button, and a sheet
    per order. A single invoice and a batch of twenty take the same path."""
    cfg = cfg or {}
    rates = fetch_eur_rates()
    sheets = "\n".join(_sheet(o, cfg, rates) for o in orders) or \
        "<div class='sheet'><p>No orders to invoice.</p></div>"
    button = "Print / Save as PDF" if len(orders) < 2 else \
        f"Print / Save {len(orders)} invoices as PDF"
    # Giveaway items have no price of their own, so a batch is the same value
    # typed over and over. One field fills every sheet that is still empty.
    fill = "" if len(orders) < 2 else (
        '<div class="fill">Unit value for all: <span class="sym">\u20ac</span>'
        '<input class="ed val" id="fillAll" type="number" step="0.01" min="0" '
        'placeholder="0.00"> <button class="link" onclick="fillAll()">'
        'apply to all</button></div>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{_STYLE}</style></head><body>
<div class="bar noprint">
  <button onclick="window.print()">{_e(button)}</button>
  {fill}
  {f'<div class="note">{_e(note)}</div>' if note else ''}
</div>
{sheets}
<script>{_SCRIPT}</script>
</body></html>"""


def invoice_html(o: dict, cfg: dict | None = None) -> str:
    """A print-ready commercial/customs invoice for one order: seller it came
    FROM, you it goes TO (your own address, from config), the forwarder it went
    VIA (the myUS-style address on the order), and an editable declared value
    so a giveaway win carries its real worth, not £0."""
    return _document([o], cfg, f"Pro forma invoice {o.get('order_id') or ''}")


def invoices_html(orders: list, cfg: dict | None = None,
                  note: str = "") -> str:
    """One document holding an invoice per order, each on its own sheet of
    paper — the forwarder wants one per parcel, and printing them one by one
    from twenty open tabs is the thing this avoids."""
    n = len(orders)
    title = f"Pro forma invoices ({n})" if n != 1 else \
        f"Pro forma invoice {orders[0].get('order_id') or ''}"
    return _document(orders, cfg, title, note)


if __name__ == "__main__":
    import chrome
    orders = read_open_orders(int(chrome.load_config().get("debug_port", 9222)))
    print(json.dumps([{k: v for k, v in o.items()
                       if k not in ("ship_to", "total_cache")}
                      for o in orders], indent=2, ensure_ascii=False))
