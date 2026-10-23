/* Calendar text follows server time; reward handlers remain in CalendarView. */
(function (calendar) {
    function monthLayout(date) {
        var year = date.getFullYear(), month = date.getMonth();
        var first = (new Date(year, month, 1).getDay() + 6) % 7;
        var days = new Date(year, month + 1, 0).getDate();
        var cells = [];
        for (var index = 0; index < 42; index++) {
            var day = index - first + 1;
            var solar = calendar.Solar.fromYmd(year, month + 1, 1).next(day - 1);
            var lunar = solar.getLunar();
            cells.push({
                day: solar.getDay(), current: day > 0 && day <= days,
                caption: lunar.getJieQi() || lunar.getFestivals()[0] || solar.getFestivals()[0] ||
                    (lunar.getDay() === 1 ? lunar.getMonthInChinese() + "月" : lunar.getDayInChinese())
            });
        }
        var currentLunar = calendar.Solar.fromYmd(year, month + 1, date.getDate()).getLunar();
        return {year: year, month: month + 1, first: first, days: days, cells: cells,
            lunarYear: currentLunar.getYearInGanZhi() + currentLunar.getYearShengXiao() + "年"};
    }

    function label(parent, text, x, y, width, size, color, align) {
        var field = new egret.TextField();
        field.text = String(text);
        field.x = x; field.y = y; field.width = width;
        field.size = size; field.bold = true; field.textColor = color;
        field.textAlign = align || "center";
        field.touchEnabled = false;
        parent.addChild(field);
    }

    function handNumber(parent, sheet, sourceFirst, sourceDays, number, x, y, scale) {
        var text = String(number);
        if (number < 1 || number > sourceDays) {
            label(parent, text, x, y, 44, 20, 0x59634c);
            return;
        }
        var sourceIndex = sourceFirst + number - 1;
        var sourceX = 21 + sourceIndex % 7 * 73;
        var width = text.length === 1 ? 32 : 44;
        var inset = text.length === 1 ? 19 : 13;
        var bitmap = new egret.Bitmap(sheet.createTexture("number" + number,
            sourceX + inset, 188 + Math.floor(sourceIndex / 7) * 65, width, 26));
        bitmap.x = x; bitmap.y = y;
        bitmap.scaleX = scale; bitmap.scaleY = scale;
        parent.addChild(bitmap);
    }

    function paint(view, layout, handwritingTexture) {
        var canvas = new egret.DisplayObjectContainer();
        var sourceFirst = (new Date(2025, layout.month - 1, 1).getDay() + 6) % 7;
        var sourceDays = new Date(2025, layout.month, 0).getDate();
        var sourceSheet = new egret.SpriteSheet(handwritingTexture);
        var heading = new egret.Bitmap(sourceSheet.createTexture("month", 0, 80, 160, 38));
        heading.x = 0; heading.y = 80;
        canvas.addChild(heading);
        label(canvas, layout.lunarYear, 393, 91, 139, 18, 0x59634c, "right");
        handNumber(canvas, sourceSheet, sourceFirst, sourceDays, Math.floor(layout.year / 100), 248, 585, 0.65);
        handNumber(canvas, sourceSheet, sourceFirst, sourceDays, layout.year % 100, 272, 583, 0.65);
        for (var index = 0; index < 42; index++) {
            var cell = layout.cells[index];
            var x = 21 + index % 7 * 73, y = 190 + Math.floor(index / 7) * 65;
            var sourceIndex = sourceFirst + cell.day - 1;
            var numberTexture = sourceSheet.createTexture("day" + cell.day,
                21 + sourceIndex % 7 * 73,
                188 + Math.floor(sourceIndex / 7) * 65, 70, 26);
            var number = new egret.Bitmap(numberTexture);
            number.x = x; number.y = y - 2;
            number.alpha = cell.current ? 1 : 0.38;
            canvas.addChild(number);
            label(canvas, cell.caption, x, y + 27, 70, cell.caption.length > 4 ? 12 : 16,
                cell.current ? 0x59634c : 0xcdbd98);
        }
        var texture = new egret.RenderTexture();
        texture.drawToTexture(canvas, new egret.Rectangle(0, 0, 553, 648));
        view.imagePic.source = texture;
        view.offlineCalendarTexture = texture;
    }

    function refresh(view) {
        var layout = monthLayout(new Date(core.Time.getServerTime() * 1000));
        var handwritingTexture = view.imagePic.texture;
        if (!handwritingTexture) {
            view.once(egret.Event.ENTER_FRAME, function () { refresh(this); }, view);
            return;
        }
        view.imagePic.width = 553; view.imagePic.height = 648;
        paint(view, layout, handwritingTexture);
    }

    var original = CalendarView.prototype.childrenCreated;
    CalendarView.prototype.childrenCreated = function () {
        original.call(this);
        refresh(this);
        this.once(egret.Event.REMOVED_FROM_STAGE, function () {
            if (this.offlineCalendarTexture) {
                this.offlineCalendarTexture.dispose();
                this.offlineCalendarTexture = null;
            }
        }, this);
    };
    // The next-month constructor handles December and leap February directly.
    CalendarModel.prototype.getMonthMaxDay = function () {
        var date = new Date(core.Time.getServerTime() * 1000);
        return new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
    };
})(module.exports);
