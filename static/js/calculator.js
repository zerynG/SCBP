/*
 * SCBP — JS-калькулятор стоимости проекта.
 *
 * Зеркало Python-модуля `mainboard/calculator.py`. Сохраняйте формулы 1:1.
 *
 * Доступ через window.SCBPCalc.* — без модульной системы, чтобы не ломать
 * существующий <script> подход в проекте.
 */
(function (global) {
    'use strict';

    var WORKING_DAYS_FALLBACK = 22;

    // -------- utils --------
    function toNum(v) {
        if (v === null || v === undefined || v === '') return null;
        var n = Number(String(v).replace(',', '.'));
        return Number.isFinite(n) ? n : null;
    }
    function num(v) {
        var n = toNum(v);
        return n === null ? 0 : n;
    }
    function parseISO(s) {
        if (!s) return null;
        // Используем явный конструктор по компонентам, чтобы избежать TZ-сюрпризов.
        var m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
        var d = new Date(s);
        return isNaN(d.getTime()) ? null : d;
    }
    function isoDay(d) {
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var dd = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + dd;
    }
    function formatCurrency(v) {
        if (v === null || v === undefined || !Number.isFinite(v)) return '—';
        return v.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₽';
    }

    // -------- calendar --------
    function isWorkDay(d, calendars) {
        if (!calendars) return d.getDay() >= 1 && d.getDay() <= 5;
        var y = d.getFullYear();
        var yearCal = calendars[y] || calendars[String(y)] || null;
        if (!yearCal) return d.getDay() >= 1 && d.getDay() <= 5;
        var m = String(d.getMonth() + 1);
        var day = String(d.getDate());
        var status = (yearCal[m] || {})[day];
        if (status === undefined || status === null) {
            return d.getDay() >= 1 && d.getDay() <= 5;
        }
        return status === 'work';
    }

    function countBusinessDays(start, end, calendars) {
        if (!start || !end) return 0;
        var s = (start instanceof Date) ? start : parseISO(start);
        var e = (end instanceof Date) ? end : parseISO(end);
        if (!s || !e || e < s) return 0;
        var d = new Date(s);
        var count = 0;
        while (d <= e) {
            if (isWorkDay(d, calendars)) count++;
            d.setDate(d.getDate() + 1);
        }
        return count;
    }

    function workingDaysInMonth(refDate, calendars, fallback) {
        var d = (refDate instanceof Date) ? refDate : parseISO(refDate);
        var fb = (fallback === null || fallback === undefined) ? WORKING_DAYS_FALLBACK : Number(fallback) || WORKING_DAYS_FALLBACK;
        if (!d) return fb;
        var first = new Date(d.getFullYear(), d.getMonth(), 1);
        var last = new Date(d.getFullYear(), d.getMonth() + 1, 0);
        var n = countBusinessDays(first, last, calendars);
        return n > 0 ? n : fb;
    }

    function countCalendarDays(start, end) {
        var s = (start instanceof Date) ? start : parseISO(start);
        var e = (end instanceof Date) ? end : parseISO(end);
        if (!s || !e || e < s) return 0;
        return Math.floor((e - s) / 86400000) + 1;
    }

    function countUnavailableBusinessDays(scheduleEntries, periodStart, periodEnd, calendars) {
        if (!scheduleEntries || !scheduleEntries.length) return 0;
        var pStart = (periodStart instanceof Date) ? periodStart : parseISO(periodStart);
        var pEnd = (periodEnd instanceof Date) ? periodEnd : parseISO(periodEnd);
        if (!pStart || !pEnd || pEnd < pStart) return 0;
        var unavail = new Set();
        scheduleEntries.forEach(function (entry) {
            var s = parseISO(entry.start);
            var e = parseISO(entry.end);
            if (!s || !e) return;
            var from = s < pStart ? pStart : s;
            var to = e > pEnd ? pEnd : e;
            var d = new Date(from);
            while (d <= to) {
                if (isWorkDay(d, calendars)) unavail.add(isoDay(d));
                d.setDate(d.getDate() + 1);
            }
        });
        return unavail.size;
    }

    function equipmentMonths(start, end) {
        var s = (start instanceof Date) ? start : parseISO(start);
        var e = (end instanceof Date) ? end : parseISO(end);
        if (!s || !e || e < s) return 0;
        var months = (e.getFullYear() - s.getFullYear()) * 12 + (e.getMonth() - s.getMonth());
        if (e.getDate() >= s.getDate()) months += 1;
        return Math.max(1, months);
    }

    // -------- margin/tax --------
    function taxMultiplier(taxPercent) {
        return 1 + num(taxPercent) / 100;
    }
    function marginMultiplier(marginPercent) {
        return 1 + num(marginPercent) / 100;
    }
    function resolveMargin(overrideMargin, projectMargin) {
        // null/undefined → projectMargin; 0 → strict 0
        if (overrideMargin === null || overrideMargin === undefined) return num(projectMargin);
        return num(overrideMargin);
    }
    function applyTaxAndMargin(baseNoTax, taxPercent, marginPercent) {
        var cost = num(baseNoTax) * taxMultiplier(taxPercent);
        var finalCost = cost * marginMultiplier(marginPercent);
        return { cost: cost, finalCost: finalCost };
    }
    
    /**
     * Начисляется ли маржа на этот тип ресурса.
     * Совпадает с Python: margin_applies().
     */
    function marginApplies(resourceType) {
        return resourceType === 'employee' || resourceType === 'contractor';
    }

    // -------- base calc per type --------
    function calcEmployeeBase(monthlySalary, start, end, calendars, unavailableDays, fallbackDays) {
        var salary = num(monthlySalary);
        var s = (start instanceof Date || start === null) ? start : parseISO(start);
        var e = (end instanceof Date || end === null) ? end : parseISO(end);
        if (s && e && e >= s) {
            var workDays = countBusinessDays(s, e, calendars);
            workDays = Math.max(0, workDays - Math.max(0, unavailableDays || 0));
            var totalDays = workingDaysInMonth(s, calendars, fallbackDays);
            var months = workDays > 0 && totalDays > 0 ? (workDays / totalDays) : 0;
            return { baseNoTax: salary * months, workDays: workDays, months: months, totalDaysInMonth: totalDays };
        }
        return { baseNoTax: salary, workDays: null, months: null, totalDaysInMonth: null };
    }

    function calcContractorBase(hourlyRate, workingHours, estimatedHours, estimatedCost) {
        var rate = num(hourlyRate);
        // workingHours передан явно (включая 0) → rate × workingHours
        if (workingHours !== null && workingHours !== undefined && workingHours !== '') {
            var h = parseInt(workingHours, 10);
            if (!Number.isFinite(h)) h = 0;
            return { baseNoTax: rate * h, workHours: h };
        }
        if (rate > 0) {
            var eh = parseInt(estimatedHours || 0, 10) || 0;
            return { baseNoTax: rate * eh, workHours: eh };
        }
        return { baseNoTax: num(estimatedCost), workHours: null };
    }

    function calcSubcontractorBase(pricingType, contractAmount, hourlyRate, workingHours, estimatedHours) {
        var rate = num(hourlyRate);
        if (pricingType === 'hourly' && rate > 0) {
            var hours;
            if (workingHours !== null && workingHours !== undefined && workingHours !== '') {
                hours = parseInt(workingHours, 10);
                if (!Number.isFinite(hours)) hours = 0;
            } else {
                hours = parseInt(estimatedHours || 0, 10) || 0;
            }
            return { baseNoTax: rate * hours, workHours: hours };
        }
        return { baseNoTax: num(contractAmount), workHours: null };
    }

    function calcEquipmentBase(pricingType, rate, quantity, start, end, fallbackTotalCost, opts) {
        opts = opts || {};
        var calendars = opts.calendars || {};
        var hourBasis = opts.hourBasis || 'calendar';
        var whPerDay = (opts.workHoursPerDay === undefined || opts.workHoursPerDay === null) ? 8 : Number(opts.workHoursPerDay) || 8;
        var qty = parseInt(quantity || 1, 10) || 1;
        var r = num(rate);
        var fallback = num(fallbackTotalCost);
        var result = {
            baseNoTax: fallback,
            monthsCount: null,
            calendarDays: null,
            calendarHours: null,
            calendarMinutes: null,
            hourBasis: hourBasis,
        };
        var s = (start instanceof Date || start === null) ? start : parseISO(start);
        var e = (end instanceof Date || end === null) ? end : parseISO(end);
        if (s && e && e >= s) {
            var days = countCalendarDays(s, e);
            if (pricingType === 'month') {
                var months = equipmentMonths(s, e);
                result.baseNoTax = r * months * qty;
                result.monthsCount = months;
                result.calendarDays = days;
            } else if (pricingType === 'day') {
                result.baseNoTax = r * days * qty;
                result.calendarDays = days;
            } else if (pricingType === 'hour') {
                if (hourBasis === 'working') {
                    var bd = countBusinessDays(s, e, calendars);
                    var workHours = bd * whPerDay;
                    result.baseNoTax = r * workHours * qty;
                    result.calendarDays = days;
                    result.calendarHours = workHours;
                } else {
                    var hours = days * 24;
                    result.baseNoTax = r * hours * qty;
                    result.calendarDays = days;
                    result.calendarHours = hours;
                }
            } else if (pricingType === 'minute') {
                var minutes = days * 24 * 60;
                result.baseNoTax = r * minutes * qty;
                result.calendarDays = days;
                result.calendarMinutes = minutes;
                result.calendarHours = days * 24;
            }
        }
        return result;
    }

    global.SCBPCalc = {
        WORKING_DAYS_FALLBACK: WORKING_DAYS_FALLBACK,
        toNum: toNum, num: num, parseISO: parseISO, isoDay: isoDay,
        formatCurrency: formatCurrency,
        isWorkDay: isWorkDay,
        countBusinessDays: countBusinessDays,
        workingDaysInMonth: workingDaysInMonth,
        countCalendarDays: countCalendarDays,
        countUnavailableBusinessDays: countUnavailableBusinessDays,
        equipmentMonths: equipmentMonths,
        taxMultiplier: taxMultiplier,
        marginMultiplier: marginMultiplier,
        resolveMargin: resolveMargin,
        applyTaxAndMargin: applyTaxAndMargin,
        marginApplies: marginApplies,
        calcEmployeeBase: calcEmployeeBase,
        calcContractorBase: calcContractorBase,
        calcSubcontractorBase: calcSubcontractorBase,
        calcEquipmentBase: calcEquipmentBase,
    };
})(window);
