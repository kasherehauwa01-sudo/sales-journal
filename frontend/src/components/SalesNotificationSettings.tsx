import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
type Settings = {
  enabled: boolean;
  send_time: string;
  recipients: string[];
  revision: number;
};
type Status = {
  id: number;
  sales_date: string;
  checked_at: string;
  missing_count: number;
  status: string;
  attempted_at: string | null;
  error_code: string | null;
};
const labels: Record<string, string> = {
  sent: "Отправлено",
  failed: "Ошибка отправки",
  uncertain: "Доставка не подтверждена",
  not_required: "Уведомление не требуется",
  sending: "Отправляется",
};
export function SalesNotificationSettings({
  onDirty,
}: {
  onDirty: (dirty: boolean) => void;
}) {
  const [form, setForm] = useState<Settings>(),
    [saved, setSaved] = useState(""),
    [email, setEmail] = useState(""),
    [invalid, setInvalid] = useState(""),
    [error, setError] = useState(""),
    [success, setSuccess] = useState(""),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState<Status | null>(null);
  const lock = useRef(false);
  const dirty = !!form && (JSON.stringify(form) !== saved || !!email.trim());
  useEffect(() => {
    onDirty(dirty);
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, onDirty]);
  const load = useCallback(async () => {
    try {
      const x = await api<Settings>("/sales-control/notification-settings");
      setForm((prev) => prev || x);
      setSaved((prev) => prev || JSON.stringify(x));
      setError("");
      setStatus(await api<Status | null>("/sales-control/notification-status"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    const previous = window.history.state?.idx;
    let restoring = false;
    const pop = (e: PopStateEvent) => {
      if (
        !dirty ||
        !Number.isInteger(previous) ||
        !Number.isInteger(e.state?.idx)
      )
        return;
      if (restoring) {
        restoring = false;
        e.stopImmediatePropagation();
        return;
      }
      if (
        e.state.idx !== previous &&
        !window.confirm("Есть несохранённые настройки. Покинуть страницу?")
      ) {
        e.stopImmediatePropagation();
        restoring = true;
        window.history.go(previous - e.state.idx);
      }
    };
    window.addEventListener("popstate", pop, true);
    return () => window.removeEventListener("popstate", pop, true);
  }, [dirty]);
  useEffect(() => {
    const stop = (e: MouseEvent) => {
      const link = (e.target as Element).closest?.("a[href]");
      if (
        dirty &&
        link &&
        !window.confirm("Есть несохранённые настройки. Покинуть страницу?")
      ) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("click", stop, true);
    return () => document.removeEventListener("click", stop, true);
  }, [dirty]);
  function add() {
    const values = email
      .split(/[;,\n]+/)
      .map((x) => x.trim())
      .filter(Boolean);
    const bad = values.find((x) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(x));
    if (bad) {
      setInvalid(`Проверьте адрес: ${bad}`);
      return;
    }
    if (!values.length) return;
    setForm((prev) =>
      prev
        ? {
            ...prev,
            recipients: [
              ...new Map(
                [...prev.recipients, ...values].map((x) => [
                  x.toLowerCase(),
                  x,
                ]),
              ).values(),
            ],
          }
        : prev,
    );
    setEmail("");
    setInvalid("");
    setSuccess("");
  }
  async function save() {
    if (!form || lock.current) return;
    if (email.trim()) {
      setInvalid("Добавьте адрес кнопкой «Добавить» перед сохранением.");
      return;
    }
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const x = await api<Settings>("/sales-control/notification-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      setForm(x);
      setSaved(JSON.stringify(x));
      setSuccess("Настройки уведомлений сохранены");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function retry() {
    if (
      !status ||
      busy ||
      lock.current ||
      !window.confirm(
        "Доставка могла состояться. Подтвердить возможную повторную отправку?",
      )
    )
      return;
    lock.current = true;
    setBusy(true);
    try {
      await api(`/sales-control/notifications/${status.id}/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve: true }),
      });
      setSuccess("Повтор поставлен в очередь");
      setStatus(await api<Status | null>("/sales-control/notification-status"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="panel sales-notifications">
      <h2>Настройки уведомлений</h2>
      <p className="hint">
        Одна ежедневная проверка продаж и одно сводное письмо. Часовой пояс —
        Москва.
      </p>
      {error && (
        <div role="alert">
          {error}
          {!form && <button onClick={() => void load()}>Повторить</button>}
        </div>
      )}
      {!form && !error ? (
        <p role="status">Загружаем настройки…</p>
      ) : (
        form && (
          <>
            <fieldset disabled={busy}>
              <label className="sales-toggle">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => {
                    setForm({ ...form, enabled: e.target.checked });
                    setSuccess("");
                  }}
                />{" "}
                Автоматический контроль продаж и email-уведомления
              </label>
              <label>
                Время ежедневной проверки
                <input
                  type="time"
                  value={form.send_time}
                  onChange={(e) => {
                    setForm({ ...form, send_time: e.target.value });
                    setSuccess("");
                  }}
                />
              </label>
              <label htmlFor="sales-recipient">Получатели уведомлений</label>
              <div className="sales-email-input">
                <input
                  id="sales-recipient"
                  type="text"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setInvalid("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      add();
                    }
                  }}
                  aria-invalid={!!invalid}
                  aria-describedby={invalid ? "sales-email-error" : undefined}
                  placeholder="name@example.ru"
                />
                <button type="button" onClick={add}>
                  Добавить
                </button>
              </div>
              {invalid && (
                <p id="sales-email-error" role="alert">
                  {invalid}
                </p>
              )}
              <ul className="sales-email-tags">
                {form.recipients.map((x) => (
                  <li key={x.toLowerCase()}>
                    {x}
                    <button
                      aria-label={`Удалить получателя ${x}`}
                      onClick={() => {
                        setForm({
                          ...form,
                          recipients: form.recipients.filter((y) => y !== x),
                        });
                        setSuccess("");
                      }}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
              <p className="hint">
                Используются существующие настройки SMTP. Сохранение не
                отправляет письмо. После сохранения первая проверка — в
                ближайшее будущее назначенное время.
              </p>
              <div className="button-row">
                <button
                  className="primary"
                  disabled={
                    !dirty ||
                    !form.send_time ||
                    (form.enabled && !form.recipients.length)
                  }
                  onClick={() => void save()}
                >
                  {busy ? "Сохраняем…" : "Сохранить настройки"}
                </button>
                {dirty && (
                  <span role="status">Есть несохранённые изменения</span>
                )}
              </div>
            </fieldset>
            {success && <p role="status">{success}</p>}
            {!form.enabled && (
              <p className="hint">
                Автоматический контроль отключён. Просмотр отсутствующих продаж
                доступен во вкладке «Контроль продаж».
              </p>
            )}
          </>
        )
      )}
      <section className="sales-last-check">
        <h3>Последняя автоматическая проверка</h3>
        <button
          disabled={busy}
          onClick={() =>
            void api<Status | null>("/sales-control/notification-status")
              .then(setStatus)
              .catch((e) => setError(e.message))
          }
        >
          Обновить статус
        </button>
        {status ? (
          <dl>
            <dt>Проверка</dt>
            <dd>
              {new Date(status.checked_at).toLocaleString("ru-RU", {
                timeZone: "Europe/Moscow",
              })}{" "}
              МСК
            </dd>
            <dt>Дата продаж</dt>
            <dd>{status.sales_date.split("-").reverse().join(".")}</dd>
            <dt>Без продаж</dt>
            <dd>{status.missing_count}</dd>
            <dt>Уведомление</dt>
            <dd>{labels[status.status] || "Проверка статуса"}</dd>
            <dt>Последняя попытка</dt>
            <dd>
              {status.attempted_at
                ? new Date(status.attempted_at).toLocaleString("ru-RU", {
                    timeZone: "Europe/Moscow",
                  }) + " МСК"
                : "—"}
            </dd>
            {status.error_code && (
              <>
                <dt>Причина</dt>
                <dd>
                  {status.error_code === "smtp_not_configured"
                    ? "SMTP не настроен. Обратитесь к администратору."
                    : status.status === "uncertain"
                      ? "Сервер мог принять письмо. Автоматический повтор отключён."
                      : "Не удалось подтвердить отправку. Проверьте SMTP."}
                </dd>
              </>
            )}
          </dl>
        ) : (
          <p className="hint">Автоматических проверок ещё не было.</p>
        )}
        {status && ["uncertain", "failed"].includes(status.status) && (
          <button
            disabled={busy || !form?.enabled}
            onClick={() => void retry()}
          >
            Подтвердить повтор отправки
          </button>
        )}
      </section>
    </section>
  );
}
