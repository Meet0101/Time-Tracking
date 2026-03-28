$(document).ready(function () {
    // Full-height layouts ko consistent rakhne ke liye --vh compute karein.
    function updateVh() {
        // window.innerHeight se exact viewport height milti hai.
        const vh = window.innerHeight * 0.01;
        document.documentElement.style.setProperty('--vh', `${vh}px`);
    }
    updateVh();
    window.addEventListener('resize', updateVh);

    // Global toasts (Bootstrap)
    window.showAppToast = function(message, level = "info") {
        const iconMap = {
            success: "fa-circle-check",
            danger: "fa-circle-exclamation",
            warning: "fa-triangle-exclamation",
            info: "fa-circle-info",
        };
        const icon = iconMap[level] || iconMap.info;
        const $container = $("#global-toast-container");
        if (!$container.length) return;
        const id = `toast-${Date.now()}`;
        const html = `
            <div id="${id}" class="toast align-items-center border-0 shadow-sm" role="alert" aria-live="assertive" aria-atomic="true">
                <div class="d-flex">
                    <div class="toast-body">
                        <i class="fas ${icon} me-2 text-${level}"></i>${message}
                    </div>
                    <button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
                </div>
            </div>
        `;
        $container.append(html);
        const el = document.getElementById(id);
        const toast = new bootstrap.Toast(el, { delay: 3200 });
        toast.show();
        el.addEventListener("hidden.bs.toast", () => $(el).remove());
    };

    window.getChartPalette = function() {
        const isDark = $("body").hasClass("dark-mode");
        return {
            primary: isDark ? "#818cf8" : "#4f46e5",
            info: isDark ? "#22d3ee" : "#06b6d4",
            success: isDark ? "#34d399" : "#10b981",
            warning: isDark ? "#fbbf24" : "#f59e0b",
            text: isDark ? "#cbd5e1" : "#475569",
            grid: isDark ? "rgba(148,163,184,0.25)" : "rgba(148,163,184,0.18)",
            surface: isDark ? "rgba(129,140,248,0.2)" : "rgba(79,70,229,0.15)",
        };
    };

    function syncThemeMeta() {
        const m = document.querySelector('meta[name="theme-color"]');
        if (m) {
            m.setAttribute("content", $("body").hasClass("dark-mode") ? "#0f172a" : "#4f46e5");
        }
    }

    // Dark Mode Toggle

    $("#dark-mode-toggle").click(function() {
        $("body").toggleClass("dark-mode");
        const icon = $(this).find("i");
        if ($("body").hasClass("dark-mode")) {
            icon.removeClass("fa-moon").addClass("fa-sun");
            localStorage.setItem("theme", "dark");
        } else {
            icon.removeClass("fa-sun").addClass("fa-moon");
            localStorage.setItem("theme", "light");
        }
        syncThemeMeta();
        window.dispatchEvent(new Event("themeChanged"));
    });

    if (localStorage.getItem("theme") === "dark") {
        $("body").addClass("dark-mode");
        $("#dark-mode-toggle i").removeClass("fa-moon").addClass("fa-sun");
    }
    syncThemeMeta();

    // Sidebar Toggle

    $("#menu-toggle").click(function (e) {
        e.preventDefault();
        if (window.innerWidth <= 768) {
            $("body").toggleClass("sidebar-open");
        } else {
            $("#wrapper").toggleClass("toggled");
        }
    });
    $("#sidebar-overlay").click(function () {
        $("body").removeClass("sidebar-open");
    });
    $("#sidebar-wrapper a").click(function () {
        if (window.innerWidth <= 768) {
            $("body").removeClass("sidebar-open");
        }
    });

    // CSRF Utility (cookie + fallback hidden input)
    function getCSRF() {
        // Hidden input fallback (base.html me always hoga)
        const hidden = $('input[name="csrfmiddlewaretoken"]').val();
        if (hidden) return hidden;

        // Cookie fallback
        const name = 'csrftoken';
        const cookieString = document.cookie || '';
        const cookies = cookieString.split(';').map(c => c.trim());
        for (let i = 0; i < cookies.length; i++) {
            if (cookies[i].startsWith(name + '=')) {
                return decodeURIComponent(cookies[i].substring(name.length + 1));
            }
        }
        return '';
    }

    // Live Timer Update
    let timerInterval;
    const startLiveTimer = (startTime) => {
        clearInterval(timerInterval);
        timerInterval = setInterval(() => {
            const now = new Date();
            const start = new Date(startTime);
            const diff = now - start;
            const hours = Math.floor(diff / 3600000);
            const minutes = Math.floor((diff % 3600000) / 60000);
            const seconds = Math.floor((diff % 60000) / 1000);
            $("#live-timer-display").text(`${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`);
        }, 1000);
    };

    // AJAX Timer Management
    $(".btn-start-timer").click(function () {
        const taskId = $("#timer-task-select").val();
        if (!taskId) {
            showAppToast("Select a task first!", "warning");
            return;
        }

        $.ajax({
            url: "/core/api/timer/start/",
            method: "POST",
            data: {
                task_id: taskId,
                csrfmiddlewaretoken: getCSRF()
            },
            success: function (response) {
                if (response.status === "ok") {
                    location.reload();
                } else {
                    showAppToast(response.error || "Unable to start timer.", "danger");
                }
            }
        });
    });

    $("#btn-stop-timer").click(function () {
        $.ajax({
            url: "/core/api/timer/stop/",
            method: "POST",
            data: {
                csrfmiddlewaretoken: getCSRF()
            },
            success: function (response) {
                if (response.status === "ok") {
                    location.reload();
                } else {
                    showAppToast(response.error || "Unable to stop timer.", "danger");
                }
            }
        });
    });

    // Check if active timer exists
    const activeStartTime = $("#live-timer-display").data("start");
    if (activeStartTime) {
        startLiveTimer(activeStartTime);
        $("#timer-status-badge").addClass("bg-danger").removeClass("bg-secondary").text("Tracking Live");

        // Idle detection: activity events se last-active timestamp update hota rahega.
        let lastActivityTs = Date.now();
        let lastNotifiedIdleMinutes = 0;
        const touchActivity = () => { lastActivityTs = Date.now(); };
        ["mousemove", "keydown", "click", "scroll", "touchstart"].forEach(evt => {
            window.addEventListener(evt, touchActivity, { passive: true });
        });

        setInterval(() => {
            const idleMinutes = Math.floor((Date.now() - lastActivityTs) / 60000);
            if (idleMinutes >= 15 && idleMinutes > lastNotifiedIdleMinutes) {
                lastNotifiedIdleMinutes = idleMinutes;
                $.ajax({
                    url: "/core/api/timer/idle-ping/",
                    method: "POST",
                    data: {
                        idle_minutes: idleMinutes,
                        csrfmiddlewaretoken: getCSRF()
                    }
                });
                showAppToast(`Idle warning: ${idleMinutes} min inactive`, "warning");
            }
        }, 60000);
    }

    // Kanban Drag & Drop
    const kanbanCards = document.querySelectorAll('.kanban-card');
    const kanbanColumns = document.querySelectorAll('.kanban-column');

    // Drag ke time click modal na khule isliye flag
    window.__kanbanDragging = false;

    kanbanCards.forEach(card => {
        card.addEventListener('dragstart', () => {
            card.classList.add('dragging');
            window.__kanbanDragging = true;
        });
        card.addEventListener('dragend', () => {
            card.classList.remove('dragging');
            // Drop ke turant baad click fired ho sakta hai
            setTimeout(() => { window.__kanbanDragging = false; }, 150);
        });
    });

    kanbanColumns.forEach(column => {
        column.addEventListener('dragover', e => {
            e.preventDefault();
            const draggingCard = document.querySelector('.dragging');
            column.querySelector('.kanban-items').appendChild(draggingCard);
        });

        column.addEventListener('drop', e => {
            const cardId = document.querySelector('.dragging').dataset.id;
            const newStatus = column.dataset.status;

            $.ajax({
                url: "/core/api/task/update-status/",
                method: "POST",
                data: {
                    task_id: cardId,
                    status: newStatus,
                    csrfmiddlewaretoken: getCSRF()
                },
                success: function (response) {
                    if (response.status !== "ok") {
                        showAppToast("Task status update failed.", "danger");
                        location.reload();
                    }
                }
            });
        });
    });

    // Task click -> status modal
    $(document).on("click", ".kanban-card", function() {
        if (window.__kanbanDragging) return;

        const $card = $(this);
        const taskId = $card.data("id");

        $("#taskModalTitle").text($card.data("title") || "");
        const project = $card.data("project") || "";
        const priority = $card.data("priority") || "";
        const deadline = $card.data("deadline") || "";
        const projectUrl = $card.data("project-url") || "/core/projects/";
        $("#taskModalMeta").text(`${project} • ${priority}${deadline ? " • " + deadline : ""}`);
        $("#taskModalDesc").text($card.data("desc") || "");
        $("#taskModalOpenProject").attr("href", projectUrl);

        $("#taskModalStatus").val($card.data("status") || "todo");
        $("#taskModalSaveStatus").data("task-id", taskId);

        const modalEl = document.getElementById("taskDetailModal");
        if (modalEl) {
            const modal = new bootstrap.Modal(modalEl);
            modal.show();
        }
    });

    // Project link click pe task modal open na ho.
    $(document).on("click", ".task-project-link", function(e) {
        e.stopPropagation();
    });

    $("#taskModalSaveStatus").click(function() {
        const taskId = $(this).data("task-id");
        const newStatus = $("#taskModalStatus").val();
        $.ajax({
            url: "/core/api/task/update-status/",
            method: "POST",
            data: {
                task_id: taskId,
                status: newStatus,
                csrfmiddlewaretoken: getCSRF()
            },
            success: function(response) {
                if (response.status === "ok") {
                    window.location.reload();
                } else {
                    showAppToast(response.error || "Update failed!", "danger");
                }
            },
            error: function() {
                showAppToast("Update failed!", "danger");
            }
        });
    });

    // Direct task open via query param (?task=ID)
    const selectedTaskEl = document.getElementById("selected-task-id");
    if (selectedTaskEl) {
        const selectedTaskId = parseInt(selectedTaskEl.textContent || "0", 10);
        if (selectedTaskId) {
            const targetCard = document.querySelector(`.kanban-card[data-id="${selectedTaskId}"]`);
            if (targetCard) {
                setTimeout(() => targetCard.click(), 200);
            }
        }
    }

    // Notifications Dropdown mark as read
    function renderNotifDropdown(items) {
        const $dropdown = $("#notif-dropdown");
        if (!$dropdown.length) return;
        let html = "";
        if (!items || !items.length) {
            html += `<li class="text-center text-muted py-2">No new notifications</li>`;
        } else {
            items.forEach((n) => {
                const weight = n.is_read ? "" : "fw-bold";
                html += `
                    <li>
                        <div role="button" tabindex="0" class="dropdown-item py-2 notification-item ${weight}" data-redirect="${n.redirect_url}">
                            <div class="small text-wrap">${n.message}</div>
                            <div class="text-muted small">${n.time_ago}</div>
                        </div>
                    </li>
                `;
            });
        }
        html += `<li><hr class="dropdown-divider"></li><li><a class="dropdown-item text-center fw-semibold" href="/core/notifications/">View all notifications</a></li>`;
        $dropdown.html(html);
    }

    function refreshNotificationFeed() {
        $.ajax({
            url: "/core/api/notifications/feed/",
            method: "GET",
            success: function(res) {
                const count = parseInt(res.unread_count || 0, 10);
                if (count > 0) {
                    $("#notif-count").text(count).removeClass("d-none").show();
                } else {
                    $("#notif-count").text(0).addClass("d-none").hide();
                }
                renderNotifDropdown(res.items || []);
            }
        });
    }

    refreshNotificationFeed();
    setInterval(refreshNotificationFeed, 30000);

    // Notification dropdown ke liye mark-as-read:
    // base.html me bell icon wala element anchor hai (dropdown-toggle class nahi hai).
    $("a[data-bs-toggle='dropdown']").on("shown.bs.dropdown", function() {
        if ($(this).find(".fas.fa-bell").length > 0) {
            $.ajax({
                url: "/core/notifications/",
                method: "POST",
                data: { csrfmiddlewaretoken: getCSRF() },
                success: function() {
                    $("#notif-count").text(0).hide();
                    refreshNotificationFeed();
                }
            });
        }
    });

    // Notification item click: mark as read + redirect
    $(document).on("click keypress", ".notification-item", function(e) {
        if (e.type === "keypress" && e.key !== "Enter") return;

        const redirectUrl = $(this).data("redirect") || "/core/tasks/";
        $.ajax({
            url: "/core/notifications/",
            method: "POST",
            data: { csrfmiddlewaretoken: getCSRF() },
            success: function() {
                window.location.href = redirectUrl;
            }
        });
    });

});
