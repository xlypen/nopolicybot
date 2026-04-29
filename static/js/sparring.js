(function () {
  var chatSel = document.getElementById("sparringChat");
  var weekInp = document.getElementById("sparringWeek");
  var btnLoad = document.getElementById("sparringLoad");
  var btnRe = document.getElementById("sparringRecompute");
  var btnFight = document.getElementById("sparringFight");
  var selA = document.getElementById("fighterA");
  var selB = document.getElementById("fighterB");
  var statusEl = document.getElementById("sparringStatus");
  var logEl = document.getElementById("sparringLog");
  var roundLiveEl = document.getElementById("sparringRoundLive");
  var mountA = document.getElementById("fighterMountA");
  var mountB = document.getElementById("fighterMountB");
  var finaleEl = document.getElementById("sparringFinale");
  var finaleTag = document.getElementById("sparringFinaleTag");
  var finaleName = document.getElementById("sparringFinaleName");
  var btnElimRun = document.getElementById("sparringElimRun");
  var elimStatus = document.getElementById("sparringElimStatus");
  var elimResult = document.getElementById("sparringEliminationResult");
  var elimPicker = document.getElementById("sparringTourneyPicker");
  var elimHint = document.getElementById("sparringElimRosterHint");
  var btnElimAll = document.getElementById("sparringElimSelectAll");
  var btnElimNone = document.getElementById("sparringElimSelectNone");
  var tabDuel = document.getElementById("sparringTabDuel");
  var tabTours = document.getElementById("sparringTabTournaments");
  var panelDuel = document.getElementById("sparringPanelDuel");
  var panelTours = document.getElementById("sparringPanelTournaments");

  var roster = [];
  var ringEl = document.getElementById("sparringRing");

  /* Минимальная длительность всего боя; один раунд ~ в 2 раза длиннее базового (см. totalMs * 840). */
  var MIN_FIGHT_MS = 20000;

  /* Замеряем реальную дистанцию между центрами спрайтов и кладём в CSS-переменные.
     CSS-анимации используют var(--sp-step-x) и var(--sp-reach-x) → удар всегда
     долетает до соперника на любой ширине арены (раньше cqi+min() обрезались
     на широких экранах и фигуры не сходились). */
  function measureImpactDistance() {
    if (!mountA || !mountB || !ringEl) return;
    var spA = mountA.querySelector(".fighter-sprite-wrap");
    var spB = mountB.querySelector(".fighter-sprite-wrap");
    if (!spA || !spB) return;
    var rA = spA.getBoundingClientRect();
    var rB = spB.getBoundingClientRect();
    var aCx = rA.left + rA.width / 2;
    var bCx = rB.left + rB.width / 2;
    var gap = Math.max(40, bCx - aCx);
    /* Кулак/нога должны "касаться" корпуса соперника, не пробивая насквозь.
       Импакт-точка — на расстоянии gap минус половина спрайта (~50px). */
    var reach = Math.max(60, gap - 64);
    /* Делим путь: 70% — шаг корпусом, 30% — выпад руки/ноги. */
    var step = Math.round(reach * 0.7);
    var arm = reach - step;
    ringEl.style.setProperty("--sp-step-x", step + "px");
    ringEl.style.setProperty("--sp-reach-x", reach + "px");
    ringEl.style.setProperty("--sp-arm-x", arm + "px");
  }

  /* Импакт-всплеск (искры) в точке между спрайтами. */
  function spawnImpactBurst(elDef, isCrit, isRage) {
    if (!elDef) return;
    var wrap = elDef.querySelector(".fighter-sprite-wrap");
    if (!wrap) return;
    var burst = document.createElement("div");
    var cls = "sparring-impact-burst";
    if (isRage) cls += " is-rage";
    else if (isCrit) cls += " is-crit";
    burst.className = cls;
    var n = isRage ? 10 : isCrit ? 8 : 6;
    for (var i = 0; i < n; i++) {
      var sp = document.createElement("span");
      var ang = (i / n) * 360 + (Math.random() * 30 - 15);
      var dist = (isRage ? 46 : isCrit ? 36 : 28) + Math.random() * 10;
      sp.style.setProperty("--ang", ang + "deg");
      sp.style.setProperty("--dist", dist + "px");
      burst.appendChild(sp);
    }
    wrap.appendChild(burst);
    window.setTimeout(function () {
      if (burst.parentNode) burst.parentNode.removeChild(burst);
    }, 700);
  }

  /* Тряска ринга на импакте. Усиливается для крита и ярости. */
  function shakeRing(intensity) {
    if (!ringEl) return;
    ringEl.style.setProperty("--sp-shake-amp", intensity + "px");
    ringEl.classList.remove("is-shake");
    /* reflow → перезапуск keyframes */
    void ringEl.offsetWidth;
    ringEl.classList.add("is-shake");
  }

  /* Hit-stop: на импакте коротко "замораживаем" анимацию для ощущения веса. */
  function hitStop(ms) {
    [mountA, mountB].forEach(function (m) {
      if (!m) return;
      var sp = m.querySelector(".fighter-sprite");
      var wr = m.querySelector(".fighter-sprite-wrap");
      [sp, wr].forEach(function (el) {
        if (!el) return;
        el.style.animationPlayState = "paused";
      });
    });
    window.setTimeout(function () {
      [mountA, mountB].forEach(function (m) {
        if (!m) return;
        var sp = m.querySelector(".fighter-sprite");
        var wr = m.querySelector(".fighter-sprite-wrap");
        [sp, wr].forEach(function (el) {
          if (!el) return;
          el.style.animationPlayState = "";
        });
      });
    }, ms);
  }

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg || "";
  }

  function mondayISO(d) {
    d = new Date(d.getTime());
    var day = d.getDay();
    var diff = (day + 6) % 7;
    d.setDate(d.getDate() - diff);
    return d.toISOString().slice(0, 10);
  }

  function mondayOfDateInput() {
    if (weekInp && weekInp.value) {
      return mondayISO(new Date(weekInp.value + "T12:00:00"));
    }
    return mondayISO(new Date());
  }

  function resetHud(mount) {
    if (!mount) return;
    var tr = mount.querySelector(".fighter-hp-track");
    var fill = mount.querySelector(".fighter-hp-fill");
    var lab = mount.querySelector(".fighter-hp-label");
    var rageFill = mount.querySelector(".fighter-rage-fill");
    var stamFill = mount.querySelector(".fighter-stam-fill");
    var stamTr = mount.querySelector(".fighter-stam-track");
    if (fill) fill.style.width = "100%";
    if (lab) lab.textContent = "—";
    if (tr) tr.classList.remove("is-low", "is-mid");
    if (rageFill) rageFill.style.width = "0%";
    if (stamFill) stamFill.style.width = "100%";
    if (stamTr) stamTr.classList.remove("is-low");
  }

  function setHpForMount(mount, current, hpMax) {
    if (!mount || !hpMax) return;
    var tr = mount.querySelector(".fighter-hp-track");
    var fill = mount.querySelector(".fighter-hp-fill");
    var lab = mount.querySelector(".fighter-hp-label");
    var pct = Math.max(0, Math.min(100, (current / hpMax) * 100));
    if (fill) fill.style.width = pct + "%";
    if (lab) {
      lab.textContent = Math.max(0, Math.round(current)) + " / " + hpMax;
    }
    if (tr) {
      tr.classList.remove("is-low", "is-mid");
      if (pct < 28) tr.classList.add("is-low");
      else if (pct < 52) tr.classList.add("is-mid");
    }
  }

  /**
   * HP/rage с сервера приходят с ключами-user id; в JS после JSON числа могут
   * потерять точность для больших id — ищем значение по строковому ключу как в селекте.
   */
  function pickBattleStat(map, uid) {
    if (map == null) return undefined;
    var s = String(uid).trim();
    if (Object.prototype.hasOwnProperty.call(map, s)) return map[s];
    var n = Number(s);
    if (!isNaN(n) && Object.prototype.hasOwnProperty.call(map, n)) return map[n];
    for (var k in map) {
      if (Object.prototype.hasOwnProperty.call(map, k) && String(k) === s) {
        return map[k];
      }
    }
    return undefined;
  }

  function setHpBars(uidA, uidB, hpAfter, hpMax) {
    setHpForMount(mountA, pickBattleStat(hpAfter, uidA), hpMax);
    setHpForMount(mountB, pickBattleStat(hpAfter, uidB), hpMax);
  }

  function setRageForMount(mount, current, rageMax) {
    if (!mount || !rageMax) return;
    var fill = mount.querySelector(".fighter-rage-fill");
    if (!fill) return;
    var v =
      current == null || isNaN(Number(current)) ? 0 : Number(current);
    var pct = Math.max(0, Math.min(100, (v / rageMax) * 100));
    fill.style.width = pct + "%";
  }

  function setRageBars(uidA, uidB, rageAfter, rageMax) {
    if (!rageAfter || !rageMax) return;
    setRageForMount(mountA, pickBattleStat(rageAfter, uidA), rageMax);
    setRageForMount(mountB, pickBattleStat(rageAfter, uidB), rageMax);
  }

  function setStamForMount(mount, current, stamMax) {
    if (!mount || !stamMax) return;
    var fill = mount.querySelector(".fighter-stam-fill");
    var tr = mount.querySelector(".fighter-stam-track");
    if (!fill) return;
    var v = current == null || isNaN(Number(current)) ? stamMax : Number(current);
    var pct = Math.max(0, Math.min(100, (v / stamMax) * 100));
    fill.style.width = pct + "%";
    if (tr) {
      if (pct < 25) tr.classList.add("is-low");
      else tr.classList.remove("is-low");
    }
  }

  function setStamBars(uidA, uidB, stamAfter, stamMax) {
    if (!stamAfter || !stamMax) return;
    setStamForMount(mountA, pickBattleStat(stamAfter, uidA), stamMax);
    setStamForMount(mountB, pickBattleStat(stamAfter, uidB), stamMax);
  }

  function hideFinale() {
    if (!finaleEl) return;
    finaleEl.classList.remove("is-visible", "is-ko");
    finaleEl.setAttribute("aria-hidden", "true");
  }

  function showFinale(isKo, winnerDisplayName) {
    if (!finaleEl || !finaleTag || !finaleName) return;
    finaleTag.textContent = isKo ? "K.O.!" : "WIN!";
    finaleName.textContent = winnerDisplayName ? "Победитель: " + winnerDisplayName : "";
    finaleEl.classList.toggle("is-ko", !!isKo);
    finaleEl.classList.add("is-visible");
    finaleEl.setAttribute("aria-hidden", "false");
  }

  function showDamageFloater(defMount, amount, isCrit, isRage) {
    if (!defMount) return;
    var wrap = defMount.querySelector(".fighter-sprite-wrap");
    if (!wrap) return;
    var el = document.createElement("div");
    var cls = "floater-dmg";
    if (isRage) cls += " is-rage";
    else if (isCrit) cls += " is-crit";
    el.className = cls;
    if (isRage) {
      el.textContent =
        (isCrit ? "ЯРОСТЬ! КРИТ! " : "ЯРОСТЬ! ") + amount;
    } else {
      el.textContent = (isCrit ? "КРИТ! " : "−") + amount;
    }
    wrap.appendChild(el);
    window.setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 2000);
  }

  /** Мастерство техники для анимации атакующего: сила + скорость + точность. */
  function masteryTierFromFighter(f) {
    if (!f || !f.stats) return "mid";
    var st = f.stats;
    var m =
      (Number(st.power) + Number(st.speed) + Number(st.accuracy)) / 3;
    if (m < 45) return "low";
    if (m < 72) return "mid";
    return "high";
  }

  function fillSelect(sel, items, emptyLabel) {
    sel.innerHTML = "";
    var o0 = document.createElement("option");
    o0.value = "";
    o0.textContent = emptyLabel || "—";
    sel.appendChild(o0);
    items.forEach(function (f) {
      var o = document.createElement("option");
      o.value = String(f.user_id);
      var st = f.stats || {};
      o.textContent =
        f.display_name + " (сила " + (st.power != null ? st.power : "?") + ")";
      sel.appendChild(o);
    });
  }

  function applyFighterToMount(mount, fighter) {
    if (!mount || !fighter) return;
    var nameEl = mount.querySelector(".fighter-name");
    var statsEl = mount.querySelector(".fighter-stats");
    var img = mount.querySelector(".fighter-avatar");
    var body = mount.querySelector("[data-part=body]");
    resetHud(mount);
    if (nameEl) nameEl.textContent = fighter.display_name || fighter.user_id;
    if (statsEl) {
      statsEl.innerHTML = "";
      var s = fighter.stats || {};
      var labels = [
        ["Сила", s.power],
        ["Защита", s.defense],
        ["Скорость", s.speed],
        ["Точность", s.accuracy],
        ["Харизма", s.charisma],
        ["Удача", s.luck],
      ];
      labels.forEach(function (pair) {
        var li = document.createElement("li");
        li.textContent = pair[0] + ": " + pair[1];
        statsEl.appendChild(li);
      });
    }
    if (img) {
      img.src = "/avatar/" + encodeURIComponent(fighter.user_id);
      img.alt = fighter.display_name || "";
    }
    var limbs = mount.querySelector(".fighter-limbs");
    if (body) {
      var v = Number(fighter.body_variant) || 0;
      var variant = v % 4;
      body.className = "fighter-body fighter-body--" + variant;
      if (limbs) {
        limbs.className = "fighter-limbs fighter-limbs--" + variant;
      }
      var hue = Number(fighter.tint_hue);
      var hueCss =
        !isNaN(hue) && hue !== 0
          ? "hue-rotate(" + hue + "deg) saturate(1.15)"
          : "";
      body.style.filter = hueCss;
      if (limbs) limbs.style.filter = hueCss;
    }
  }

  function updateFightEnabled() {
    var ok =
      chatSel &&
      chatSel.value &&
      selA &&
      selB &&
      selA.value &&
      selB.value &&
      selA.value !== selB.value;
    if (btnFight) btnFight.disabled = !ok;
  }

  function getSelectedFighters() {
    var a = roster.filter(function (f) {
      return String(f.user_id) === selA.value;
    })[0];
    var b = roster.filter(function (f) {
      return String(f.user_id) === selB.value;
    })[0];
    return { a: a, b: b };
  }

  function refreshMountsFromSelect() {
    var fb = getSelectedFighters();
    applyFighterToMount(mountA, fb.a);
    applyFighterToMount(mountB, fb.b);
    updateFightEnabled();
  }

  function escapeHtml(text) {
    var d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function clearEliminationUi() {
    if (elimResult) elimResult.innerHTML = "";
    if (elimStatus) elimStatus.textContent = "";
  }

  function setSparringTab(which) {
    var duel = which === "duel";
    if (tabDuel) {
      tabDuel.classList.toggle("is-active", duel);
      tabDuel.setAttribute("aria-selected", duel ? "true" : "false");
    }
    if (tabTours) {
      tabTours.classList.toggle("is-active", !duel);
      tabTours.setAttribute("aria-selected", !duel ? "true" : "false");
    }
    if (panelDuel) {
      panelDuel.classList.toggle("is-active", duel);
      if (duel) panelDuel.removeAttribute("hidden");
      else panelDuel.setAttribute("hidden", "");
    }
    if (panelTours) {
      panelTours.classList.toggle("is-active", !duel);
      if (!duel) panelTours.removeAttribute("hidden");
      else panelTours.setAttribute("hidden", "");
    }
  }

  function renderTourneyPicker() {
    if (!elimPicker) return;
    elimPicker.innerHTML = "";
    if (!roster.length) {
      if (elimHint) elimHint.textContent = "Сначала загрузите ростер.";
      updateElimToolbar();
      return;
    }
    if (elimHint) elimHint.textContent = "Отметьте участников (минимум 2).";
    roster.forEach(function (f) {
      var lab = document.createElement("label");
      lab.className = "sparring-tourney-cb-label";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = String(f.user_id);
      cb.addEventListener("change", updateElimToolbar);
      var span = document.createElement("span");
      span.textContent = f.display_name || String(f.user_id);
      lab.appendChild(cb);
      lab.appendChild(span);
      elimPicker.appendChild(lab);
    });
    updateElimToolbar();
  }

  function getElimCheckedIds() {
    if (!elimPicker) return [];
    var cbs = elimPicker.querySelectorAll('input[type="checkbox"]:checked');
    return Array.prototype.map.call(cbs, function (x) {
      return Number(x.value);
    });
  }

  function updateElimToolbar() {
    if (!btnElimRun) return;
    var sel = getElimCheckedIds().length;
    btnElimRun.disabled = roster.length < 2 || sel < 2 || !chatSel || !chatSel.value;
  }

  function renderEliminationResult(data) {
    if (!elimResult) return;
    if (!data || !data.ok || !data.elimination) {
      elimResult.innerHTML = "<p>Нет данных.</p>";
      return;
    }
    var el = data.elimination;
    var h =
      '<div class="sparring-bracket-champion">Победитель: <strong>' +
      escapeHtml(el.champion_name || el.champion_user_id) +
      "</strong></div>";
    if (el.starting_order && el.starting_order.length) {
      h += "<p><strong>Порядок после жребия:</strong> ";
      h += el.starting_order
        .map(function (x) {
          return escapeHtml(x.name || x.user_id);
        })
        .join(", ");
      h += "</p>";
    }
    var rounds = el.bracket_rounds || [];
    for (var ri = 0; ri < rounds.length; ri++) {
      var rd = rounds[ri];
      h += '<div class="sparring-bracket-round"><div class="sparring-bracket-round-title">Раунд ' + rd.round_index + "</div>";
      var ms = rd.matches || [];
      for (var mi = 0; mi < ms.length; mi++) {
        var m = ms[mi];
        if (m.type === "bye") {
          h +=
            '<div class="sparring-bracket-match sparring-bracket-match--bye">Пропуск: ' +
            escapeHtml(m.bye_name || m.bye_user_id) +
            "</div>";
        } else {
          h += '<div class="sparring-bracket-match">';
          h +=
            escapeHtml(m.name_a) +
            " vs " +
            escapeHtml(m.name_b) +
            " → <strong>" +
            escapeHtml(m.winner_name) +
            "</strong>";
          h += " <span class=\"sparring-bracket-fatigue\">(" + m.rounds_in_fight + " обменов; усталость до боя " +
            escapeHtml(JSON.stringify(m.fatigue_before)) +
            " → после " +
            escapeHtml(JSON.stringify(m.fatigue_after)) +
            ")</span>";
          h += "</div>";
        }
      }
      h += "</div>";
    }
    if (el.fatigue_final) {
      h +=
        '<div class="sparring-fatigue-final"><strong>Итоговая усталость участников:</strong> ' +
        escapeHtml(JSON.stringify(el.fatigue_final)) +
        (el.fatigue_step != null ? " (шаг +" + el.fatigue_step + " за бой)" : "") +
        "</div>";
    }
    if (el.fatigue_rules) {
      h += "<p class=\"sparring-elim-note\">" + escapeHtml(el.fatigue_rules) + "</p>";
    }
    elimResult.innerHTML = h;
  }

  async function runEliminationTournament() {
    if (!chatSel || !chatSel.value) {
      if (elimStatus) elimStatus.textContent = "Выберите чат и загрузите ростер.";
      return;
    }
    var ids = getElimCheckedIds();
    if (ids.length < 2) {
      if (elimStatus) elimStatus.textContent = "Отметьте минимум двух участников.";
      return;
    }
    var week = mondayOfDateInput();
    if (elimStatus) elimStatus.textContent = "Симуляция турнира…";
    if (btnElimRun) btnElimRun.disabled = true;
    try {
      var r = await fetch("/api/sparring/tournament_elimination", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: Number(chatSel.value),
          week: week,
          user_ids: ids,
        }),
      });
      var data = await r.json();
      if (!data.ok) {
        if (elimStatus) elimStatus.textContent = data.error || "Ошибка";
        if (elimResult) elimResult.innerHTML = "";
        return;
      }
      if (elimStatus) elimStatus.textContent = "Готово.";
      renderEliminationResult(data);
    } catch (e) {
      if (elimStatus) elimStatus.textContent = "Сеть: " + e;
    } finally {
      updateElimToolbar();
    }
  }

  async function loadRoster() {
    if (!chatSel || !chatSel.value) {
      setStatus("Выберите чат");
      return;
    }
    var week = mondayOfDateInput();
    if (weekInp) weekInp.value = week;
    setStatus("Загрузка…");
    try {
      var url =
        "/api/sparring/roster?chat_id=" +
        encodeURIComponent(chatSel.value) +
        "&week=" +
        encodeURIComponent(week);
      var r = await fetch(url, { credentials: "same-origin" });
      var data = await r.json();
      if (!data.ok) {
        setStatus(data.error || "Ошибка");
        roster = [];
        fillSelect(selA, [], "—");
        fillSelect(selB, [], "—");
        renderTourneyPicker();
        clearEliminationUi();
        return;
      }
      roster = data.fighters || [];
      clearEliminationUi();
      fillSelect(selA, roster, "— боец A —");
      fillSelect(selB, roster, "— боец B —");
      setStatus("Бойцов: " + roster.length);
      if (logEl) {
        logEl.textContent =
          roster.length === 0
            ? "За эту неделю нет строк. Нажмите «Пересчитать из БД» (нужны сообщения в messages)."
            : "Выберите двух бойцов и нажмите «В бой».";
      }
      if (roundLiveEl) roundLiveEl.textContent = "";
      hideFinale();
      refreshMountsFromSelect();
      renderTourneyPicker();
    } catch (e) {
      setStatus("Сеть: " + e);
      roster = [];
      renderTourneyPicker();
    }
  }

  async function recompute() {
    if (!chatSel || !chatSel.value) {
      setStatus("Выберите чат");
      return;
    }
    var week = mondayOfDateInput();
    if (weekInp) weekInp.value = week;
    setStatus("Пересчёт…");
    try {
      var r = await fetch("/api/sparring/recompute", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: Number(chatSel.value), week: week }),
      });
      var data = await r.json();
      if (!data.ok) {
        setStatus(data.error || "Ошибка");
        return;
      }
      setStatus("Записано строк: " + data.inserted);
      await loadRoster();
    } catch (e) {
      setStatus("Сеть: " + e);
    }
  }

  function clearAnim() {
    [mountA, mountB].forEach(function (m) {
      if (!m) return;
      m.classList.remove(
        "fighter-punch",
        "fighter-kick",
        "fighter-hit",
        "fighter-dodge",
        "fighter-parry",
        "fighter-heavy",
        "fighter-combo",
        "fighter-sweep",
        "fighter-rage-attack",
        "fighter-second-wind",
        "fighter-mastery-low",
        "fighter-mastery-mid",
        "fighter-mastery-high"
      );
      m.style.removeProperty("--sp-round-dur");
      var w = m.querySelector(".fighter-sprite-wrap");
      if (w) {
        w.classList.remove("fighter-wrap-advance", "fighter-wrap-brace");
      }
    });
  }

  /* Маппинг типа атаки → CSS-класс на атакующего и текстовая метка для лога. */
  var ATTACK_TYPE_INFO = {
    light:  { cls: "fighter-punch", label: "удар", tag: "Лёгкий" },
    rage:   { cls: "fighter-rage-attack", label: "прыжок-удар сверху", tag: "Ярость" },
    heavy:  { cls: "fighter-heavy", label: "тяжёлый свинг", tag: "Свинг" },
    combo:  { cls: "fighter-combo", label: "комбо (двойной)", tag: "Комбо" },
    sweep:  { cls: "fighter-sweep", label: "подсечка", tag: "Подсечка" },
  };

  /* Длительность раунда зависит от типа: combo/heavy чуть длиннее, rage заметно длиннее. */
  function durationForRound(roundMs, attackType, isDodge, isParry) {
    if (isDodge) return Math.max(700, Math.round(roundMs * 0.85));
    if (isParry) return Math.max(820, Math.round(roundMs * 1.0));
    if (attackType === "rage") return Math.max(roundMs + 1400, Math.round(roundMs * 2.2));
    if (attackType === "heavy") return Math.max(roundMs + 200, Math.round(roundMs * 1.15));
    if (attackType === "combo") return Math.max(roundMs + 280, Math.round(roundMs * 1.25));
    if (attackType === "sweep") return Math.max(roundMs, Math.round(roundMs * 1.0));
    return Math.max(760, roundMs + 320);
  }

  function playRound(
    round,
    uidA,
    uidB,
    hpMax,
    rageMax,
    stamMax,
    roundMs,
    roundIndex,
    totalRounds,
    useKick,
    fighterA,
    fighterB,
    done
  ) {
    clearAnim();
    /* Сервер: attacker = order[r%2], order = [ua, ub] как user_a, user_b в запросе. */
    var ri = Number(round.i);
    if (isNaN(ri)) ri = 1;
    var attackerIsA = (ri - 1) % 2 === 0;
    var dmg = round.damage || 0;
    var crit = !!round.crit;
    var rageStrike = !!round.rage;
    var dodged = !!round.dodged;
    var parried = !!round.parried;
    var attackType = round.attack_type || (rageStrike ? "rage" : (useKick ? "light" : "light"));
    var elAtt = attackerIsA ? mountA : mountB;
    var elDef = attackerIsA ? mountB : mountA;
    var fAtt = attackerIsA ? fighterA : fighterB;
    var fDef = attackerIsA ? fighterB : fighterA;
    var durMs = durationForRound(roundMs, attackType, dodged, parried);
    var dur = durMs + "ms";
    [mountA, mountB].forEach(function (m) {
      if (m) m.style.setProperty("--sp-round-dur", dur);
    });
    /* Tier мастерства даёт более «острое» easing у топовых статистов. */
    if (elAtt) {
      var tier = masteryTierFromFighter(fAtt);
      elAtt.classList.add("fighter-mastery-" + tier);
    }

    if (dodged) {
      /* Атакующий замахивается → защитник уклоняется и ничего не происходит. */
      if (elAtt) {
        var wa = elAtt.querySelector(".fighter-sprite-wrap");
        if (wa) wa.classList.add("fighter-wrap-advance");
        elAtt.classList.add(attackType === "light" ? (useKick ? "fighter-kick" : "fighter-punch") : "fighter-" + (attackType === "rage" ? "rage-attack" : attackType));
      }
      if (elDef) {
        elDef.classList.add("fighter-dodge");
      }
    } else if (parried) {
      /* Атакующий бьёт → защитник парирует и наносит контрудар. */
      if (elAtt) {
        var wa2 = elAtt.querySelector(".fighter-sprite-wrap");
        if (wa2) wa2.classList.add("fighter-wrap-advance");
        elAtt.classList.add(useKick ? "fighter-kick" : "fighter-punch");
      }
      if (elDef) {
        elDef.classList.add("fighter-parry");
      }
    } else {
      /* Обычный удар: атакующий идёт вперёд, защитник bracе/recoil. */
      var info = ATTACK_TYPE_INFO[attackType] || ATTACK_TYPE_INFO.light;
      if (elAtt) {
        var wa3 = elAtt.querySelector(".fighter-sprite-wrap");
        if (wa3) wa3.classList.add("fighter-wrap-advance");
        if (attackType === "light") {
          elAtt.classList.add(useKick ? "fighter-kick" : "fighter-punch");
        } else {
          elAtt.classList.add(info.cls);
        }
      }
      if (elDef) {
        var wd = elDef.querySelector(".fighter-sprite-wrap");
        if (wd) wd.classList.add("fighter-wrap-brace");
      }
    }

    if (roundLiveEl) {
      var actionLabel;
      var tagCls;
      if (dodged) {
        actionLabel = "уклонение!";
        tagCls = "is-dodge";
      } else if (parried) {
        actionLabel = "парирование + контрудар";
        tagCls = "is-parry";
      } else if (attackType === "rage") {
        actionLabel = "прыжок-удар сверху (ярость)";
        tagCls = "is-rage";
      } else if (attackType === "heavy") {
        actionLabel = "тяжёлый свинг";
        tagCls = "is-heavy";
      } else if (attackType === "combo") {
        actionLabel = "комбо (двойной удар)";
        tagCls = "is-combo";
      } else if (attackType === "sweep") {
        actionLabel = "подсечка";
        tagCls = "is-sweep";
      } else {
        actionLabel = useKick ? "удар ногой" : "удар рукой";
        tagCls = "is-light";
      }
      var dmgPart = "";
      if (dodged) {
        dmgPart = ", урон 0";
      } else if (parried) {
        dmgPart = ", контрудар " + (round.parry_damage || 0);
      } else {
        dmgPart = ", урон " + dmg + (crit ? " (крит!)" : "");
      }
      var chancePart = "";
      if (round.chance_dodge != null && round.chance_parry != null) {
        var pd = Math.round((round.chance_dodge || 0) * 100);
        var pp = Math.round((round.chance_parry || 0) * 100);
        var pc = Math.round((round.chance_crit || 0) * 100);
        chancePart =
          " — шансы: уклон " + pd + "%, парир. " + pp + "%, крит " + pc + "%";
      }
      var html =
        "Раунд " + roundIndex + " / " + totalRounds + ": " +
        '<span class="sparring-attack-tag ' + tagCls + '">' + escapeHtml(actionLabel) + "</span>" +
        escapeHtml(dmgPart) + escapeHtml(chancePart);
      roundLiveEl.innerHTML = html;
    }

    /* Импакт-момент совпадает с пиком сближения (40–46% длительности). */
    var hitDelay = (rageStrike || attackType === "heavy")
      ? Math.round(durMs * 0.46)
      : (parried ? Math.round(durMs * 0.5) : Math.round(durMs * 0.42));

    if (dodged) {
      /* Промах: только обновим бары без вспышки. */
      window.setTimeout(function () {
        if (round.hp_after) setHpBars(uidA, uidB, round.hp_after, hpMax);
        if (round.rage_after && rageMax) setRageBars(uidA, uidB, round.rage_after, rageMax);
        if (round.stamina_after && stamMax) setStamBars(uidA, uidB, round.stamina_after, stamMax);
      }, hitDelay);
    } else {
      /* На комбо: первая искра в 32%, вторая (главный импакт) в 46%. */
      if (attackType === "combo") {
        window.setTimeout(function () {
          spawnImpactBurst(elDef, false, false);
          shakeRing(2);
          if (elDef) elDef.classList.add("fighter-hit");
        }, Math.round(durMs * 0.32));
      }
      window.setTimeout(function () {
        if (parried) {
          /* При парировании урон получает АТАКУЮЩИЙ. */
          showDamageFloater(elAtt, round.parry_damage || 0, false, false);
          spawnImpactBurst(elAtt, false, false);
          shakeRing(4);
          hitStop(70);
          if (elAtt) elAtt.classList.add("fighter-hit");
        } else {
          var burstCrit = crit;
          var burstRage = rageStrike;
          showDamageFloater(elDef, dmg, crit, rageStrike);
          spawnImpactBurst(elDef, burstCrit, burstRage);
          shakeRing(rageStrike ? 8 : crit ? 5 : (attackType === "heavy" ? 6 : 3));
          hitStop(rageStrike ? 110 : crit ? 80 : (attackType === "heavy" ? 90 : 55));
          if (elDef) elDef.classList.add("fighter-hit");
        }
        if (round.hp_after) setHpBars(uidA, uidB, round.hp_after, hpMax);
        if (round.rage_after && rageMax) setRageBars(uidA, uidB, round.rage_after, rageMax);
        if (round.stamina_after && stamMax) setStamBars(uidA, uidB, round.stamina_after, stamMax);
        /* Второе дыхание: подсветить ауру у того, у кого сработало. */
        if (round.second_wind) {
          var sw = round.second_wind;
          for (var k in sw) {
            if (!Object.prototype.hasOwnProperty.call(sw, k)) continue;
            var swMount = (String(k) === String(uidA)) ? mountA : mountB;
            if (swMount) {
              swMount.classList.add("fighter-second-wind");
              window.setTimeout((function (mm) {
                return function () { mm.classList.remove("fighter-second-wind"); };
              })(swMount), 1100);
            }
          }
        }
      }, hitDelay);
    }
    window.setTimeout(function () {
      clearAnim();
      done();
    }, durMs);
  }

  async function fight() {
    var fb = getSelectedFighters();
    if (!fb.a || !fb.b) return;
    applyFighterToMount(mountA, fb.a);
    applyFighterToMount(mountB, fb.b);
    /* Замер делаем после applyFighterToMount → DOM устаканился, спрайты на местах. */
    measureImpactDistance();
    var week = mondayOfDateInput();
    if (weekInp) weekInp.value = week;
    btnFight.disabled = true;
    hideFinale();
    setStatus("Бой…");
    if (logEl) logEl.textContent = "Сервер считает раунды…";
    if (roundLiveEl) roundLiveEl.textContent = "";
    try {
      var r = await fetch("/api/sparring/fight", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: Number(chatSel.value),
          week: week,
          user_a: String(selA.value).trim(),
          user_b: String(selB.value).trim(),
        }),
      });
      var data = await r.json();
      if (!data.ok) {
        if (logEl) logEl.textContent = data.error || "Ошибка";
        setStatus("");
        btnFight.disabled = false;
        return;
      }
      var uidA = String(selA.value).trim();
      var uidB = String(selB.value).trim();
      var rounds = data.rounds || [];
      var hpMax = Number(data.hp_max) || 160;
      var rageMax = Number(data.rage_max) || 100;
      var stamMax = Number(data.stamina_max) || 100;
      if (rounds.length === 0) {
        if (logEl) logEl.textContent = "Пустой бой.";
        setStatus("");
        btnFight.disabled = false;
        return;
      }
      var initial = {};
      initial[uidA] = hpMax;
      initial[uidB] = hpMax;
      setHpBars(uidA, uidB, initial, hpMax);
      var rage0 = {};
      rage0[uidA] = 0;
      rage0[uidB] = 0;
      setRageBars(uidA, uidB, rage0, rageMax);
      var stam0 = {};
      stam0[uidA] = stamMax;
      stam0[uidB] = stamMax;
      setStamBars(uidA, uidB, stam0, stamMax);
      if (logEl) {
        logEl.textContent =
          "Бой: " +
          rounds.length +
          " раундов. Типы атак: лёгкий/тяжёлый/комбо/подсечка/ярость; защитник может уклониться или парировать с контрударом. Стамина (синяя) тратится на атаку и удар; при <25% эффективная атака/защита снижается.";
      }
      var totalMs = Math.max(MIN_FIGHT_MS, Math.round(rounds.length * 840));
      var roundMs = Math.max(1040, Math.floor(totalMs / rounds.length));
      var i = 0;
      function next() {
        if (i >= rounds.length) {
          window.setTimeout(function () {
            var wname =
              String(data.winner_user_id) === uidA
                ? fb.a.display_name
                : fb.b.display_name;
            var last = rounds[rounds.length - 1];
            var hpLoser =
              last &&
              last.hp_after &&
              typeof data.loser_user_id !== "undefined"
                ? pickBattleStat(last.hp_after, data.loser_user_id)
                : null;
            var isKo = hpLoser != null && hpLoser <= 0;
            if (roundLiveEl) roundLiveEl.textContent = "";
            showFinale(isKo, wname);
            if (logEl) {
              var upsetNote = "";
              if (
                data.weaker_user_id != null &&
                Number(data.winner_user_id) === Number(data.weaker_user_id)
              ) {
                upsetNote = data.underdog_fortune
                  ? " Редкая фортуна — победа аутсайдера!"
                  : " Победа более слабого по сумме статов.";
              }
              logEl.textContent =
                (isKo ? "Нокаут! " : "Победа по очкам! ") +
                "Победитель: " +
                wname +
                " (id " +
                data.winner_user_id +
                "). Всего раундов: " +
                rounds.length +
                "." +
                upsetNote;
            }
            setStatus("Готово");
            btnFight.disabled = false;
          }, 640);
          return;
        }
        /*
         * Атакующий чередуется A,B,A,B… При i%2 нога всегда у одного и того же бойца.
         * Цикл из 4: кулак, нога, нога, кулак — у обоих со временем и руки, и ноги.
         */
        var strikeTypeCycle = [false, true, true, false];
        var useKickThis = strikeTypeCycle[i % 4];
        playRound(
          rounds[i],
          uidA,
          uidB,
          hpMax,
          rageMax,
          stamMax,
          roundMs,
          i + 1,
          rounds.length,
          useKickThis,
          fb.a,
          fb.b,
          function () {
            i += 1;
            next();
          }
        );
      }
      next();
    } catch (e) {
      if (logEl) logEl.textContent = "Сеть: " + e;
      setStatus("");
      btnFight.disabled = false;
    }
  }

  if (btnLoad) btnLoad.addEventListener("click", loadRoster);
  if (btnRe) btnRe.addEventListener("click", recompute);
  if (btnFight) btnFight.addEventListener("click", fight);
  if (selA) selA.addEventListener("change", refreshMountsFromSelect);
  if (selB) selB.addEventListener("change", refreshMountsFromSelect);
  if (chatSel) {
    chatSel.addEventListener("change", function () {
      updateFightEnabled();
      clearEliminationUi();
    });
  }
  if (weekInp && !weekInp.value) {
    weekInp.value = mondayISO(new Date());
  }
  /* При ресайзе арены пересчитываем дистанцию импакта. */
  window.addEventListener("resize", measureImpactDistance);
  if (window.ResizeObserver && ringEl) {
    var ro = new ResizeObserver(measureImpactDistance);
    ro.observe(ringEl);
  }
  if (btnElimRun) btnElimRun.addEventListener("click", runEliminationTournament);
  if (btnElimAll && elimPicker) {
    btnElimAll.addEventListener("click", function () {
      elimPicker.querySelectorAll('input[type="checkbox"]').forEach(function (c) {
        c.checked = true;
      });
      updateElimToolbar();
    });
  }
  if (btnElimNone && elimPicker) {
    btnElimNone.addEventListener("click", function () {
      elimPicker.querySelectorAll('input[type="checkbox"]').forEach(function (c) {
        c.checked = false;
      });
      updateElimToolbar();
    });
  }
  document.querySelectorAll("[data-sparring-tab]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var w = btn.getAttribute("data-sparring-tab");
      if (w) setSparringTab(w);
    });
  });
  renderTourneyPicker();
})();
