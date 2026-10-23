const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../..');
const target = path.join(root, 'client/js/main.offline.stage6i2.js');
const begin = '\n/* OFFLINE_PERPETUAL_CALENDAR_BEGIN */\n';
const end = '\n/* OFFLINE_PERPETUAL_CALENDAR_END */';
let source = fs.readFileSync(target, 'utf8');
const start = source.indexOf(begin);
if (start >= 0) {
    const finish = source.indexOf(end, start);
    if (finish < 0) throw new Error('Calendar bundle end missing');
    source = source.slice(0, start) + source.slice(finish + end.length);
}
const license = fs.readFileSync(path.join(root, 'client/js/vendor/lunar.LICENSE'), 'utf8');
const vendor = fs.readFileSync(path.join(root, 'client/js/vendor/lunar.js'), 'utf8');
const calendar = fs.readFileSync(path.join(root, 'client/js/calendar.offline.js'), 'utf8');
const bundle = begin + '(function () {\nvar module = {exports: {}}, define;\n/*\n' +
    license + '\n*/\n' + vendor + '\n' + calendar + '\n})();' + end;
const updated = source.trimEnd() + bundle + '\n';
if (updated !== fs.readFileSync(target, 'utf8')) {
    const backup = target + '.before_perpetual_calendar';
    if (!fs.existsSync(backup)) fs.copyFileSync(target, backup);
    fs.writeFileSync(target, updated);
}
console.log('Calendar bundled into ' + target);
