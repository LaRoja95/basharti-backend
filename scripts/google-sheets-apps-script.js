/**
 * Basharti — ربط Google Sheet بالطلبات
 *
 * 1. افتحي الـ Sheet → Extensions → Apps Script
 * 2. الصقي هذا الكود بالكامل
 * 3. غيّري WEBHOOK_SECRET (نفس القيمة في Easypanel)
 * 4. Deploy → New deployment → Web app
 *    - Execute as: Me
 *    - Who has access: Anyone
 * 5. انسخي رابط الـ Web App → GOOGLE_SHEETS_WEBHOOK_URL في Easypanel
 */

const WEBHOOK_SECRET = "ضعي-سر-قوي-هنا";
const HEADERS = [
  "التاريخ",
  "رقم الطلب",
  "الاسم",
  "الجوال",
  "المنطقة",
  "المدينة",
  "العنوان",
  "المنتجات",
  "المجموع الفرعي",
  "التوصيل",
  "الإجمالي",
  "الحالة",
];

function ensureHeaders_(sheet) {
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight("bold");
    sheet.setFrozenRows(1);
  }
}

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents || "{}");
    if (!body.secret || body.secret !== WEBHOOK_SECRET) {
      return jsonResponse({ ok: false, error: "unauthorized" }, 401);
    }
    if (!body.row || !Array.isArray(body.row)) {
      return jsonResponse({ ok: false, error: "invalid row" }, 400);
    }

    const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    ensureHeaders_(sheet);
    sheet.appendRow(body.row);

    return jsonResponse({ ok: true, orderId: body.orderId || "" });
  } catch (err) {
    return jsonResponse({ ok: false, error: String(err) }, 500);
  }
}

function jsonResponse(obj, code) {
  const output = ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(
    ContentService.MimeType.JSON
  );
  if (code) {
    // Apps Script has no native status codes; include code in payload for debugging.
    obj.httpStatus = code;
  }
  return output;
}
