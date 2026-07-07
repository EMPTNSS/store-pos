// Навигационная оболочка вкладок (этап 2.5).
// Только показ/скрытие панелей и реестр открытых вкладок — никакой денежной/складской
// логики (она на сервере). Панели живут в DOM: переключение вкладок = show/hide, поэтому
// введённое в поля не теряется. Контент раздела грузится по HTMX один раз при открытии.
(function () {
    "use strict";

    var tabs = document.getElementById("tabs");
    var panels = document.getElementById("panels");
    var launcher = document.getElementById("launcher");

    // Реестр открытых разделов: key -> true. «cashier» — постоянная вкладка, всегда открыта.
    var open = { cashier: true };

    function panelEl(key) {
        return document.getElementById("panel-" + key);
    }
    function tabEl(key) {
        return tabs.querySelector('.tab[data-key="' + key + '"]');
    }

    // Активировать вкладку: скрыть все панели/вкладки, показать выбранную.
    function activate(key) {
        var i;
        var allTabs = tabs.querySelectorAll(".tab");
        for (i = 0; i < allTabs.length; i++) allTabs[i].classList.remove("active");
        var allPanels = panels.querySelectorAll(".panel");
        for (i = 0; i < allPanels.length; i++) allPanels[i].classList.remove("active");

        var t = tabEl(key);
        var p = panelEl(key);
        if (t) t.classList.add("active");
        if (p) p.classList.add("active");
    }

    // Открыть раздел: если вкладка уже есть — просто активировать (одна вкладка на раздел),
    // иначе создать вкладку + панель и один раз подгрузить содержимое по HTMX.
    function openSection(key, title, url) {
        if (open[key]) {
            activate(key);
            return;
        }
        open[key] = true;

        var tab = document.createElement("div");
        tab.className = "tab";
        tab.setAttribute("data-key", key);
        tab.appendChild(document.createTextNode(title + " "));

        var close = document.createElement("button");
        close.className = "close";
        close.type = "button";
        close.title = "Закрыть вкладку";
        close.textContent = "×"; // ×
        close.addEventListener("click", function (e) {
            e.stopPropagation(); // клик по крестику не активирует вкладку
            closeSection(key);
        });
        tab.appendChild(close);
        tab.addEventListener("click", function () {
            activate(key);
        });
        tabs.appendChild(tab);

        var panel = document.createElement("section");
        panel.className = "panel";
        panel.id = "panel-" + key;
        panels.appendChild(panel);

        // Одноразовая загрузка содержимого панели. Дальше панель остаётся в DOM.
        window.htmx.ajax("GET", url, { target: panel, swap: "innerHTML" });

        activate(key);
    }

    // Закрыть раздел: удалить вкладку и панель. Незакоммиченный черновик теряется осознанно
    // (сохранность данных — сервер, вкладки — удобство). Повторное открытие грузит заново.
    function closeSection(key) {
        if (!open[key]) return;
        var t = tabEl(key);
        var p = panelEl(key);
        var wasActive = t && t.classList.contains("active");
        if (t) t.parentNode.removeChild(t);
        if (p) p.parentNode.removeChild(p);
        delete open[key];
        if (wasActive) activate("cashier"); // касса — всегда доступный запасной вариант
    }

    // Карточка товара (этап 3.1): одна вкладка «Карточка» с подменой товара внутри
    // (roadmap 2.5) — не по вкладке на товар. Открытие другого товара заменяет содержимое.
    function openCard(productId) {
        var url = "/products/" + productId + "/card";
        var panel = panelEl("card");
        if (!open.card) {
            open.card = true;

            var tab = document.createElement("div");
            tab.className = "tab";
            tab.setAttribute("data-key", "card");
            tab.appendChild(document.createTextNode("Карточка "));

            var close = document.createElement("button");
            close.className = "close";
            close.type = "button";
            close.title = "Закрыть вкладку";
            close.textContent = "×"; // ×
            close.addEventListener("click", function (e) {
                e.stopPropagation();
                closeSection("card");
            });
            tab.appendChild(close);
            tab.addEventListener("click", function () {
                activate("card");
            });
            tabs.appendChild(tab);

            panel = document.createElement("section");
            panel.className = "panel";
            panel.id = "panel-card";
            panels.appendChild(panel);
        }
        // Загрузить/подменить карточку выбранного товара в ту же панель.
        window.htmx.ajax("GET", url, { target: panel, swap: "innerHTML" });
        activate("card");
    }

    // Делегирование: клик по «Открыть карточку» в результатах поиска раздела «Товары».
    // Панель товаров создаётся динамически, но #panels постоянен — слушаем на нём.
    panels.addEventListener("click", function (e) {
        var btn = e.target.closest ? e.target.closest("[data-product-id]") : null;
        if (btn) openCard(btn.getAttribute("data-product-id"));
    });

    // Постоянная вкладка кассы: клик активирует её.
    var cashierTab = tabEl("cashier");
    if (cashierTab) {
        cashierTab.addEventListener("click", function () {
            activate("cashier");
        });
    }

    // Модалка возврата (этап 4.1): разовое действие поверх кассы (2.5), не вкладка.
    // JS только показывает/прячет оверлей и подгружает тело; вся логика — на сервере.
    var returnBtn = document.getElementById("return-btn");
    var returnModal = document.getElementById("return-modal");
    var returnModalBody = document.getElementById("return-modal-body");

    function openReturnModal() {
        returnModal.classList.add("open");
        // Свежее тело при каждом открытии (черновик прошлого возврата уже очищен при закрытии).
        window.htmx.ajax("GET", "/returns/modal", {
            target: returnModalBody,
            swap: "innerHTML",
        });
    }

    // Закрыть: очистить черновик на сервере (разовое действие — не откладывают наполовину),
    // затем спрятать оверлей и опустошить тело.
    function closeReturnModal() {
        window.fetch("/returns/clear", { method: "POST" });
        returnModal.classList.remove("open");
        returnModalBody.innerHTML = "";
    }

    if (returnBtn) returnBtn.addEventListener("click", openReturnModal);
    if (returnModal) {
        returnModal.addEventListener("click", function (e) {
            // Клик по фону (вне карточки) или по элементу с data-return-close — закрыть.
            var closer = e.target.closest ? e.target.closest("[data-return-close]") : null;
            if (e.target === returnModal || closer) closeReturnModal();
        });
    }

    // Кнопки-лаунчеры разделов.
    var buttons = launcher.querySelectorAll("button[data-key]");
    for (var i = 0; i < buttons.length; i++) {
        (function (btn) {
            btn.addEventListener("click", function () {
                openSection(
                    btn.getAttribute("data-key"),
                    btn.getAttribute("data-title"),
                    btn.getAttribute("data-url")
                );
            });
        })(buttons[i]);
    }
})();
