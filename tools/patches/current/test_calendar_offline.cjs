const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(root, 'client/js/main.offline.stage6i2.js'), 'utf8');
const previous = fs.readFileSync(path.join(root, 'client/js/main.offline.stage6i2.js.before_perpetual_calendar'), 'utf8');
assert.equal(source.split('\n/* OFFLINE_PERPETUAL_CALENDAR_BEGIN */')[0], previous.trimEnd());
let patch = fs.readFileSync(path.join(root, 'client/js/calendar.offline.js'), 'utf8');
patch = patch.replace('var original = CalendarView.prototype.childrenCreated;',
    'globalThis.calendarLayout = monthLayout; globalThis.calendarPaint = paint; var original = CalendarView.prototype.childrenCreated;');
const lunar = require(path.join(root, 'client/js/vendor/lunar.js'));
function Model() {}
function View() {}
function Container() { this.children = []; }
Container.prototype.addChild = function (child) { this.children.push(child); };
function Sheet(texture) { this.texture = texture; this.entries = {}; }
Sheet.prototype.createTexture = function (name, x, y, width, height) {
    return this.entries[name] = {source: this.texture, x, y, width, height};
};
const ctx = {module: {exports: lunar}, CalendarModel: Model, CalendarView: View,
    core: {Time: {getServerTime: () => ctx.timestamp}},
    egret: {DisplayObjectContainer: Container, SpriteSheet: Sheet,
        TextField: function () {},
        Bitmap: function (texture) { this.texture = texture; },
        Rectangle: function () {}, RenderTexture: function () {
            this.drawToTexture = function (canvas) { this.canvas = canvas; };
        }}};
vm.createContext(ctx);
vm.runInContext(patch, ctx);
for (const [year, month, first, days] of [[2026,9,1,30], [2027,1,4,31],
    [2028,2,1,29], [2028,12,4,31], [2029,1,0,31], [2100,2,0,28]]) {
    const date = new Date(year, month - 1, 15, 12);
    ctx.timestamp = date.getTime() / 1000;
    const layout = ctx.calendarLayout(date);
    assert.equal(layout.first, first, `${year}-${month} weekday`);
    assert.equal(layout.days, days, `${year}-${month} length`);
    assert.equal(new Model().getMonthMaxDay(), days);
    assert.equal(layout.cells.filter(cell => cell.current).length, days);
    const view = {imagePic: {texture: 'handwriting'}};
    ctx.calendarPaint(view, layout, view.imagePic.texture);
    const nodes = view.imagePic.source.canvas.children;
    const bitmapNodes = nodes.filter(node => node.texture);
    const dayNumbers = bitmapNodes.slice(-42);
    for (let day = 1; day <= days; day++) {
        const index = first + day - 1;
        const number = dayNumbers[index];
        assert.equal(number.x, 21 + index % 7 * 73);
        assert.equal(number.y, 188 + Math.floor(index / 7) * 65);
        assert.equal(number.alpha, 1);
        assert.equal(number.texture.source, 'handwriting');
    }
}
const september = ctx.calendarLayout(new Date(2026, 8, 15));
assert.equal(september.cells[september.first + 24].caption, '中秋节');
const february = ctx.calendarLayout(new Date(2026, 1, 17));
assert.equal(february.cells[february.first + 16].caption, '春节');
assert.ok(february.lunarYear.includes('马'));
console.log('Calendar: year boundaries, leap years, lunar dates, 42-cell placement and unchanged reward code passed.');
