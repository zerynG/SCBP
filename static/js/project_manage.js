/*
 * SCBP project_manage — UI binding для страницы расчёта проекта.
 * Использует window.SCBPCalc для расчётов (зеркало Python-калькулятора).
 *
 * Принципы:
 *  - оптимистичный live-preview через SCBPCalc.* (мгновенный отклик)
 *  - debounced AJAX-save → сервер возвращает rows+totals, фронт догоняет
 *    их как "источник правды" (синхронизация с Python)
 *  - localStorage используется только как back-up на время до save,
 *    серверные значения имеют приоритет при первичной загрузке
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var calcLib = window.SCBPCalc;
        if (!calcLib) {
            console.error('SCBPCalc not loaded');
            return;
        }
        var PM_CONFIG = window.__PM_CONFIG__ || {};
        var CSRF_TOKEN = PM_CONFIG.csrfToken || '';
        var PROJECT_ID = Number(PM_CONFIG.projectId || 0);
        var CALENDARS = PM_CONFIG.calendars || {};
        var EMPLOYEE_SCHEDULE = PM_CONFIG.employeeSchedule || {};

        function readProjectSettings() {
            // Все настройки читаются из живых input'ов проекта, чтобы JS-расчёт
            // зеркалил сервер до debounced save.
            var whEl = document.querySelector('input[name="work_hours_per_day"]');
            var hbEl = document.querySelector('select[name="equipment_hour_basis"], input[name="equipment_hour_basis"]');
            var fbEl = document.querySelector('input[name="working_days_per_month_fallback"]');
            return {
                workHoursPerDay: whEl ? (Number(String(whEl.value).replace(',', '.')) || PM_CONFIG.workHoursPerDay || 8) : (PM_CONFIG.workHoursPerDay || 8),
                equipmentHourBasis: hbEl ? (hbEl.value || PM_CONFIG.equipmentHourBasis || 'calendar') : (PM_CONFIG.equipmentHourBasis || 'calendar'),
                workingDaysPerMonthFallback: fbEl ? (Number(fbEl.value) || PM_CONFIG.workingDaysPerMonthFallback || 22) : (PM_CONFIG.workingDaysPerMonthFallback || 22),
            };
        }

        // === Tab switching for add-panel ===
        document.querySelectorAll('.pm-add-tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                document.querySelectorAll('.pm-add-tab').forEach(function (t) { t.classList.remove('active'); });
                document.querySelectorAll('.pm-add-content').forEach(function (c) { c.classList.remove('active'); });
                tab.classList.add('active');
                var target = document.getElementById(tab.dataset.tab);
                if (target) target.classList.add('active');
            });
        });

        // === Subcontractor pricing type toggle ===
        window.toggleSubFields = function () {
            var pt = document.getElementById('sub_pricing_type');
            document.querySelectorAll('.sub-hourly-field').forEach(function (f) {
                f.style.display = pt && pt.value === 'hourly' ? '' : 'none';
            });
        };

        // === Catalog logic ===
        var resourcesRaw = localStorage.getItem('createProjectResources');
        var catalog;
        try { catalog = resourcesRaw ? JSON.parse(resourcesRaw) : {}; } catch (e) { catalog = {}; }
        if (!catalog) catalog = {};
        var catalogDb = PM_CONFIG.catalogDb || {};
        catalog.employees = (catalog.employees || []).concat(catalogDb.employees || []);
        catalog.executors = (catalog.executors || []).concat(catalogDb.contractors || []);
        catalog.contractors = (catalog.contractors || []).concat(catalogDb.subcontractors || []);
        catalog.equipment = (catalog.equipment || []).concat(catalogDb.equipment || []);

        function addOption(select, text, value) {
            var o = document.createElement('option');
            o.value = value; o.textContent = text;
            select.appendChild(o);
        }
        var todayStr = new Date().toISOString().split('T')[0];

        var empSelect = document.getElementById('catalogEmployeeSelect');
        if (empSelect) {
            addOption(empSelect, '— выберите сотрудника —', '');
            var emps = catalog.employees || [];
            if (emps.length === 0) {
                empSelect.disabled = true;
                addOption(empSelect, 'Каталог пуст', '');
            } else {
                emps.forEach(function (e, idx) {
                    addOption(empSelect, (e.name || 'Без имени') + (e.position ? ' — ' + e.position : ''), String(idx));
                });
            }
            var empBtn = document.getElementById('fillEmployeeFromCatalogBtn');
            if (empBtn) empBtn.addEventListener('click', function () {
                var idx = parseInt(empSelect.value, 10);
                var e = emps[idx]; if (!e) { alert('Выберите сотрудника'); return; }
                var salaryRaw = e.monthlySalary != null ? e.monthlySalary : (e.salary != null ? e.salary : e.monthly_salary);
                if (salaryRaw === undefined || salaryRaw === null || salaryRaw === '') {
                    alert('Нет данных о зарплате в каталоге'); return;
                }
                document.getElementById('emp_name').value = e.name || '';
                document.getElementById('emp_position').value = e.position || '';
                document.getElementById('emp_salary').value = String(parseFloat(salaryRaw));
                document.getElementById('addEmployeeForm').requestSubmit();
            });
        }

        var executorSelect = document.getElementById('catalogExecutorSelect');
        if (executorSelect) {
            addOption(executorSelect, '— выберите исполнителя —', '');
            var executors = catalog.executors || [];
            if (executors.length === 0) {
                executorSelect.disabled = true;
                addOption(executorSelect, 'Каталог пуст', '');
            } else {
                executors.forEach(function (ex, idx) {
                    var typeLabel = String(ex.contractType || ex.contract_type || '').toUpperCase();
                    addOption(executorSelect, (ex.name || 'Без имени') + (typeLabel ? ' — ' + typeLabel : ''), String(idx));
                });
            }
            var exBtn = document.getElementById('fillContractorFromCatalogBtn');
            if (exBtn) exBtn.addEventListener('click', function () {
                var idx = parseInt(executorSelect.value, 10);
                var ex = executors[idx]; if (!ex) { alert('Выберите исполнителя'); return; }
                var contractType = String(ex.contractType || ex.contract_type || 'gph').toLowerCase();
                var hoursRaw = ex.estimatedHours != null ? ex.estimatedHours : (ex.hours != null ? ex.hours : ex.estimated_hours);
                var hoursNum = (hoursRaw === '' || hoursRaw == null) ? 1 : parseInt(hoursRaw, 10);
                var rateRaw = String(ex.hourlyCost != null ? ex.hourlyCost : (ex.cost != null ? ex.cost : (ex.hourly_rate || '')));
                var rateNum = rateRaw === '' ? null : Number(rateRaw.replace(',', '.'));
                if (!rateNum || !Number.isFinite(rateNum) || rateNum <= 0) {
                    var fixedRaw = String(ex.fixedCost != null ? ex.fixedCost : (ex.contractAmount || ''));
                    var fixedNum = fixedRaw === '' ? null : Number(fixedRaw.replace(',', '.'));
                    if (fixedNum && Number.isFinite(fixedNum) && fixedNum > 0) {
                        rateNum = (hoursNum > 0) ? fixedNum / hoursNum : fixedNum;
                    }
                }
                if (!rateNum || rateNum <= 0) { alert('Нет корректной стоимости в каталоге.'); return; }
                var form = document.getElementById('addContractorForm');
                form.querySelector('[name="name"]').value = ex.name || '';
                form.querySelector('[name="contract_type"]').value = contractType;
                form.querySelector('[name="hourly_rate"]').value = String(rateNum);
                form.querySelector('[name="estimated_hours"]').value = (hoursNum > 0) ? hoursNum : 1;
                form.querySelector('[name="actual_hours"]').value = 0;
                form.requestSubmit();
            });
        }

        var subSelect = document.getElementById('catalogSubcontractorSelect');
        if (subSelect) {
            addOption(subSelect, '— выберите подрядчика —', '');
            var subs = catalog.contractors || [];
            if (subs.length === 0) {
                subSelect.disabled = true;
                addOption(subSelect, 'Каталог пуст', '');
            } else {
                subs.forEach(function (c, idx) {
                    addOption(subSelect, (c.name || 'Без названия') + (c.entityType || c.entity_type ? ' — ' + (c.entityType || c.entity_type) : ''), String(idx));
                });
            }
            var subBtn = document.getElementById('fillSubcontractorFromCatalogBtn');
            if (subBtn) subBtn.addEventListener('click', function () {
                var idx = parseInt(subSelect.value, 10);
                var c = subs[idx]; if (!c) { alert('Выберите подрядчика'); return; }
                var form = document.getElementById('addSubcontractorForm');
                var rateRaw = String(c.hourlyCost != null ? c.hourlyCost : (c.hourly_rate || ''));
                var rateNum = rateRaw === '' ? null : Number(rateRaw.replace(',', '.'));
                var fixedRaw = String(c.fixedCost != null ? c.fixedCost : (c.contractAmount != null ? c.contractAmount : c.contract_amount || ''));
                var fixedNum = fixedRaw === '' ? null : Number(fixedRaw.replace(',', '.'));
                var pType = c.pricingType || c.pricing_type || 'fixed';
                form.querySelector('[name="name"]').value = c.name || '';
                form.querySelector('[name="entity_type"]').value = c.entityType || c.entity_type || 'individual';
                form.querySelector('[name="pricing_type"]').value = pType;
                if (pType === 'hourly' && rateNum) {
                    form.querySelector('[name="contract_amount"]').value = String(rateNum);
                    var hrInp = form.querySelector('[name="hourly_rate"]');
                    if (hrInp) hrInp.value = String(rateNum);
                    var hrs = c.estimatedHours != null ? c.estimatedHours : c.estimated_hours;
                    var ehInp = form.querySelector('[name="estimated_hours"]');
                    if (ehInp && hrs) ehInp.value = hrs;
                } else if (fixedNum) {
                    form.querySelector('[name="contract_amount"]').value = String(fixedNum);
                }
                form.querySelector('[name="contract_date"]').value = todayStr;
                form.querySelector('[name="work_description"]').value = 'Из каталога';
                window.toggleSubFields();
                form.requestSubmit();
            });
        }

        var eqSelect = document.getElementById('catalogEquipmentSelect');
        if (eqSelect) {
            addOption(eqSelect, '— выберите оборудование —', '');
            var eqs = catalog.equipment || [];
            if (eqs.length === 0) {
                eqSelect.disabled = true;
                addOption(eqSelect, 'Каталог пуст', '');
            } else {
                eqs.forEach(function (e, idx) {
                    var pType = e.pricingType || e.pricing_type || '';
                    addOption(eqSelect, (e.name || 'Без названия') + (pType ? ' — ' + pType : ''), String(idx));
                });
            }
            var eqBtn = document.getElementById('fillEquipmentFromCatalogBtn');
            if (eqBtn) eqBtn.addEventListener('click', function () {
                var idx = parseInt(eqSelect.value, 10);
                var e = eqs[idx]; if (!e) { alert('Выберите оборудование'); return; }
                var form = document.getElementById('addEquipmentForm');
                var pType = String(e.pricingType || e.pricing_type || 'month').toLowerCase();
                var costVal = e.operationalCost || e.operational_cost;
                if (!costVal && pType === 'month') costVal = e.monthlyCost || e.monthly_cost;
                if (!costVal) costVal = e.cost;
                var opNum = costVal ? Number(String(costVal).replace(',', '.')) : null;
                if (!opNum || opNum <= 0) { alert('Нет корректной стоимости.'); return; }
                form.querySelector('[name="name"]').value = e.name || '';
                form.querySelector('[name="pricing_type"]').value = pType;
                form.querySelector('[name="operational_cost"]').value = String(opNum);
                form.querySelector('[name="cost"]').value = String(opNum);
                form.querySelector('[name="model"]').value = e.model || '';
                form.querySelector('[name="manufacturer"]').value = e.manufacturer || '';
                form.querySelector('[name="quantity"]').value = 1;
                form.querySelector('[name="purchase_date"]').value = todayStr;
                form.requestSubmit();
            });
        }

        // === Dynamic recalculation ===
        var fmt = calcLib.formatCurrency;
        var toNum = calcLib.toNum;

        var baseCostEl = document.getElementById('finance-base-cost');
        var finalPriceEl = document.getElementById('finance-final-price');
        var netProfitEl = document.getElementById('finance-net-profit');
        var vatEl = document.getElementById('finance-vat');
        var priceWithVatEl = document.getElementById('finance-price-with-vat');
        var vatCardEl = document.getElementById('finance-vat-card');
        var priceWithVatCardEl = document.getElementById('finance-price-with-vat-card');
        var tfootBaseEl = document.getElementById('tfoot-base-cost');
        var tfootFinalEl = document.getElementById('tfoot-final-cost');

        function currentVatRate() {
            var vatInput = document.querySelector('input[name="vat_rate"]');
            return vatInput ? (Number(String(vatInput.value).replace(',', '.')) || 0) : 0;
        }

        // Показ/скрытие НДС-карточек и вывод значений
        function applyVat(finalPrice) {
            var vatRate = currentVatRate();
            var show = vatRate > 0;
            if (vatCardEl) vatCardEl.style.display = show ? '' : 'none';
            if (priceWithVatCardEl) priceWithVatCardEl.style.display = show ? '' : 'none';
            var vatAmount = finalPrice * vatRate / 100;
            if (vatEl) vatEl.textContent = fmt(vatAmount);
            if (priceWithVatEl) priceWithVatEl.textContent = fmt(finalPrice + vatAmount);
        }

        function setRateText(el, value, unit) {
            if (!el) return;
            // безопасно: только textContent + создание <span>
            el.textContent = fmt(value);
            if (unit) {
                var sp = document.createElement('span');
                sp.className = 'rate-unit';
                sp.textContent = unit;
                el.appendChild(sp);
            }
        }

        function recalcTotals() {
            var totalBase = 0, totalFinal = 0;
            document.querySelectorAll('tr.resource-row').forEach(function (row) {
                var c = parseFloat(row.dataset.lastCost || '');
                var f = parseFloat(row.dataset.lastFinal || '');
                if (Number.isFinite(c)) totalBase += c;
                if (Number.isFinite(f)) totalFinal += f;
            });
            if (baseCostEl) baseCostEl.textContent = fmt(totalBase);
            if (tfootBaseEl) tfootBaseEl.textContent = fmt(totalBase);
            if (finalPriceEl) finalPriceEl.textContent = fmt(totalFinal);
            if (tfootFinalEl) tfootFinalEl.textContent = fmt(totalFinal);
            var profit = totalFinal - totalBase;
            if (netProfitEl) {
                netProfitEl.textContent = fmt(profit);
                netProfitEl.className = 'kpi-value ' + (profit >= 0 ? 'positive' : 'negative');
            }
            applyVat(totalFinal);
            updateSubtotals();
        }

        // Подытоги по типам ресурсов (Итого = Σ final внутри группы)
        function updateSubtotals() {
            var sums = { employee: 0, contractor: 0, subcontractor: 0, equipment: 0 };
            document.querySelectorAll('tr.resource-row').forEach(function (row) {
                var t = row.dataset.resourceType;
                var f = parseFloat(row.dataset.lastFinal || '');
                if (t in sums && Number.isFinite(f)) sums[t] += f;
            });
            Object.keys(sums).forEach(function (t) {
                var el = document.getElementById('subtotal-' + t);
                if (el) el.textContent = fmt(sums[t]);
            });
        }

        function readRowInputs(row) {
            return {
                margin: row.querySelector('input[name="resource_margin"]'),
                start: row.querySelector('input[name="work_start_date"]'),
                end: row.querySelector('input[name="work_end_date"]'),
                hours: row.querySelector('input[name="working_hours"]'),
                service: row.querySelector('select[name="service"]'),
            };
        }

        function rowMargin(row) {
            var inputs = readRowInputs(row);
            var rawMargin = inputs.margin ? inputs.margin.value : '';
            var projectMarginEl = document.querySelector('input[name="margin"]');
            // '' → используем маржу проекта; '0' → строгий 0
            if (rawMargin === '' || rawMargin === null || rawMargin === undefined) {
                return toNum(projectMarginEl && projectMarginEl.value) || 0;
            }
            var n = toNum(rawMargin);
            return n === null ? (toNum(projectMarginEl && projectMarginEl.value) || 0) : n;
        }

        function recalcRow(row) {
            var type = row.dataset.resourceType;
            var taxEl = document.querySelector('input[name="tax_rate"]');
            var taxRate = toNum(taxEl && taxEl.value);
            if (taxRate === null) taxRate = toNum(row.dataset.taxRate) || 0;
            var margin = rowMargin(row);
            var inputs = readRowInputs(row);
            var startVal = inputs.start && inputs.start.value || '';
            var endVal = inputs.end && inputs.end.value || '';
            var workDaysEl = row.querySelector('.js-work-days');
            var workHoursEl = row.querySelector('.js-work-hours');
            var monthlyCostEl = row.querySelector('.js-monthly-cost');
            var costEl = row.querySelector('.js-cost');
            var finalEl = row.querySelector('.js-final-cost');

            var baseNoTax = 0;
            var workDays = null;
            var workHours = null;

            var settings = readProjectSettings();
            if (type === 'employee') {
                var schedule = EMPLOYEE_SCHEDULE[row.dataset.resourceId] || [];
                var unavail = (startVal && endVal)
                    ? calcLib.countUnavailableBusinessDays(schedule, startVal, endVal, CALENDARS)
                    : 0;
                var emp = calcLib.calcEmployeeBase(
                    toNum(row.dataset.baseMonthlyCost) || 0,
                    startVal || null, endVal || null,
                    CALENDARS, unavail,
                    settings.workingDaysPerMonthFallback
                );
                baseNoTax = emp.baseNoTax;
                workDays = emp.workDays;
                if (workDays !== null) workHours = workDays * settings.workHoursPerDay;
                var bmc = toNum(row.dataset.baseMonthlyCost);
                if (bmc !== null) setRateText(monthlyCostEl, bmc, '/мес');
            } else if (type === 'contractor') {
                var hRate = toNum(row.dataset.hourlyRate);
                var rawHrs = inputs.hours ? inputs.hours.value : '';
                var c = calcLib.calcContractorBase(
                    hRate, rawHrs,
                    toNum(row.dataset.baseEstimatedHours),
                    toNum(row.dataset.baseEstimatedCost)
                );
                baseNoTax = c.baseNoTax;
                workHours = c.workHours;
                if (startVal && endVal) workDays = calcLib.countBusinessDays(startVal, endVal, CALENDARS);
                if (hRate) setRateText(monthlyCostEl, hRate, '/час');
            } else if (type === 'subcontractor') {
                var pricing = row.dataset.pricingType || 'fixed';
                var sRate = toNum(row.dataset.hourlyRate);
                var rawSHrs = inputs.hours ? inputs.hours.value : '';
                var s = calcLib.calcSubcontractorBase(
                    pricing,
                    toNum(row.dataset.baseContractAmount),
                    sRate, rawSHrs,
                    toNum(row.dataset.baseEstimatedHours)
                );
                baseNoTax = s.baseNoTax;
                workHours = s.workHours;
                if (startVal && endVal) workDays = calcLib.countBusinessDays(startVal, endVal, CALENDARS);
                if (pricing === 'hourly' && sRate) {
                    setRateText(monthlyCostEl, sRate, '/час');
                } else {
                    var bca = toNum(row.dataset.baseContractAmount);
                    if (bca !== null) setRateText(monthlyCostEl, bca, '/фикс');
                }
            } else if (type === 'equipment') {
                var ePricing = row.dataset.pricingType || 'month';
                var eRate = toNum(row.dataset.rate);
                var eQty = parseInt(row.dataset.quantity || '1', 10) || 1;
                var eq = calcLib.calcEquipmentBase(
                    ePricing, eRate, eQty,
                    startVal || null, endVal || null,
                    toNum(row.dataset.baseTotalCost) || 0,
                    {
                        calendars: CALENDARS,
                        hourBasis: settings.equipmentHourBasis,
                        workHoursPerDay: settings.workHoursPerDay,
                    }
                );
                baseNoTax = eq.baseNoTax;
                workDays = eq.calendarDays;
                workHours = eq.calendarHours;
                var unitMap = { month: '/мес', day: '/дн', hour: '/час', minute: '/мин' };
                if (eRate !== null) setRateText(monthlyCostEl, eRate, unitMap[ePricing] || '');
            }

            // Страховые взносы (taxRate) — только на ФОТ штатных сотрудников
            var insuranceForRow = (type === 'employee') ? taxRate : 0;
            var r = calcLib.applyTaxAndMargin(baseNoTax, insuranceForRow, margin);

            if (workDaysEl) workDaysEl.textContent = workDays !== null && workDays !== undefined ? workDays : '—';
            if (workHoursEl) workHoursEl.textContent = workHours !== null && workHours !== undefined ? workHours : '—';
            if (costEl) costEl.textContent = fmt(r.cost);
            if (finalEl) finalEl.textContent = fmt(r.finalCost);

            row.dataset.lastCost = String(r.cost || 0);
            row.dataset.lastFinal = String(r.finalCost || 0);
        }

        // === Auto-save via AJAX with debounce ===
        var debounceTimers = {};
        function showSaveIndicator(row) {
            var key = row.dataset.resourceType + '-' + row.dataset.resourceId;
            var indicator = row.querySelector('.save-indicator[data-indicator="' + key + '"]');
            if (indicator) {
                indicator.classList.remove('visible');
                void indicator.offsetWidth;
                indicator.classList.add('visible');
            }
        }

        function applyTotalsFromServer(totals) {
            if (!totals) return;
            if (baseCostEl) baseCostEl.textContent = fmt(parseFloat(totals.base_cost));
            if (tfootBaseEl) tfootBaseEl.textContent = fmt(parseFloat(totals.base_cost));
            if (finalPriceEl) finalPriceEl.textContent = fmt(parseFloat(totals.final_price));
            if (tfootFinalEl) tfootFinalEl.textContent = fmt(parseFloat(totals.final_price));
            var profit = parseFloat(totals.net_profit);
            if (netProfitEl) {
                netProfitEl.textContent = fmt(profit);
                netProfitEl.className = 'kpi-value ' + (profit >= 0 ? 'positive' : 'negative');
            }
            // НДС из ответа сервера (источник правды), с фоллбэком на клиентский расчёт
            if (totals.price_with_vat !== undefined) {
                var vatRate = parseFloat(totals.vat_rate) || 0;
                var show = vatRate > 0;
                if (vatCardEl) vatCardEl.style.display = show ? '' : 'none';
                if (priceWithVatCardEl) priceWithVatCardEl.style.display = show ? '' : 'none';
                if (vatEl) vatEl.textContent = fmt(parseFloat(totals.vat_amount));
                if (priceWithVatEl) priceWithVatEl.textContent = fmt(parseFloat(totals.price_with_vat));
            } else {
                applyVat(parseFloat(totals.final_price));
            }
        }

        function applyRowsFromServer(rows) {
            if (!rows || !rows.length) return;
            var byKey = {};
            rows.forEach(function (r) { byKey[r.resource_type + ':' + r.resource_id] = r; });
            document.querySelectorAll('tr.resource-row').forEach(function (row) {
                var key = row.dataset.resourceType + ':' + row.dataset.resourceId;
                var data = byKey[key];
                if (!data) return;
                var costEl = row.querySelector('.js-cost');
                var finalEl = row.querySelector('.js-final-cost');
                if (costEl) costEl.textContent = fmt(parseFloat(data.cost));
                if (finalEl) finalEl.textContent = fmt(parseFloat(data.final_cost));
                row.dataset.lastCost = String(parseFloat(data.cost || 0));
                row.dataset.lastFinal = String(parseFloat(data.final_cost || 0));
            });
            updateSubtotals();
        }

        function autoSaveRow(row) {
            var formId = 'update-' + row.dataset.resourceType + '-' + row.dataset.resourceId;
            var form = document.getElementById(formId);
            if (!form) return;
            var formData = new FormData(form);
            row.querySelectorAll('input[form="' + formId + '"], select[form="' + formId + '"]').forEach(function (inp) {
                if (inp.name) formData.set(inp.name, inp.value);
            });
            fetch(form.action, {
                method: 'POST',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                body: formData
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data && data.success) {
                    showSaveIndicator(row);
                    var sk = 'pm:' + PROJECT_ID + ':' + row.dataset.resourceType + ':' + row.dataset.resourceId;
                    localStorage.removeItem(sk);
                    applyRowsFromServer(data.rows);
                    applyTotalsFromServer(data.totals);
                }
            }).catch(function () { /* offline-friendly */ });
        }

        function debouncedAutoSave(row) {
            var key = row.dataset.resourceType + '-' + row.dataset.resourceId;
            clearTimeout(debounceTimers[key]);
            debounceTimers[key] = setTimeout(function () { autoSaveRow(row); }, 800);
        }

        // === Bind event handlers ===
        document.querySelectorAll('tr.resource-row').forEach(function (row) {
            var storageKey = function () {
                return 'pm:' + PROJECT_ID + ':' + row.dataset.resourceType + ':' + row.dataset.resourceId;
            };
            // localStorage применяем только к ПУСТЫМ полям — серверные значения имеют приоритет
            var loadRowFromCache = function () {
                var raw = localStorage.getItem(storageKey());
                if (!raw) return false;
                try {
                    var obj = JSON.parse(raw);
                    var inputs = readRowInputs(row);
                    var map = {
                        resource_margin: inputs.margin,
                        work_start_date: inputs.start,
                        work_end_date: inputs.end,
                        working_hours: inputs.hours,
                        service: inputs.service,
                    };
                    Object.keys(obj).forEach(function (k) {
                        var el = map[k];
                        if (el && obj[k] !== undefined && obj[k] !== '' && el.value === '') {
                            el.value = obj[k];
                        }
                    });
                    return true;
                } catch (e) { return false; }
            };
            var saveRowLocal = function () {
                var inputs = readRowInputs(row);
                var obj = {
                    service: inputs.service ? inputs.service.value : '',
                    resource_margin: inputs.margin ? inputs.margin.value : '',
                    work_start_date: inputs.start ? inputs.start.value : '',
                    work_end_date: inputs.end ? inputs.end.value : '',
                    working_hours: inputs.hours ? inputs.hours.value : '',
                };
                localStorage.setItem(storageKey(), JSON.stringify(obj));
            };
            loadRowFromCache();

            var fields = row.querySelectorAll('input[name="resource_margin"], input[name="work_start_date"], input[name="work_end_date"], input[name="working_hours"], select[name="service"]');
            fields.forEach(function (inp) {
                ['input', 'change'].forEach(function (ev) {
                    inp.addEventListener(ev, function () {
                        saveRowLocal();
                        recalcRow(row);
                        recalcTotals();
                        debouncedAutoSave(row);
                    });
                });
            });
            recalcRow(row);
        });

        // === Project tax/margin auto-save ===
        var projectSaveTimer = null;
        function saveProjectFields() {
            clearTimeout(projectSaveTimer);
            projectSaveTimer = setTimeout(function () {
                var form = document.getElementById('projectForm');
                if (!form) return;
                var formData = new FormData(form);
                fetch(form.action, {
                    method: 'POST',
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                    body: formData
                }).then(function (r) { return r.json(); }).then(function (data) {
                    if (data && data.success) {
                        // Запросим единый снимок чисел с сервера
                        fetch('/project/' + PROJECT_ID + '/recalculate/', {
                            method: 'POST',
                            headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': CSRF_TOKEN }
                        }).then(function (r) { return r.json(); }).then(function (rd) {
                            if (rd && rd.success) {
                                applyRowsFromServer(rd.rows);
                                applyTotalsFromServer(rd.totals);
                            }
                        }).catch(function () {});
                    }
                }).catch(function () {});
            }, 800);
        }
        document.querySelectorAll(
            'input[name="tax_rate"], input[name="margin"], input[name="vat_rate"], ' +
            'input[name="work_hours_per_day"], input[name="working_days_per_month_fallback"], ' +
            'select[name="equipment_hour_basis"]'
        ).forEach(function (el) {
            ['input', 'change'].forEach(function (ev) {
                el.addEventListener(ev, function () {
                    document.querySelectorAll('tr.resource-row').forEach(recalcRow);
                    recalcTotals();
                    saveProjectFields();
                });
            });
            el.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); el.blur(); }
            });
        });

        recalcTotals();
    });
})();
