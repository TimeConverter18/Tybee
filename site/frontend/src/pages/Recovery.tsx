import { FormEvent, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "../api";

type Stats = {
  messages: number;
  clients: number;
  directions: Record<string, number>;
  media: number;
  orders: number;
  orders_in_dump: number;
  orders_only_in_export: number;
  orders_backfilled?: number;
  buttons: number;
  topics: { user_id: number; messages: number; buttons: number; media: number; orders: number; first_at: string | null; last_at: string | null }[];
  media_missing: number;
  skipped: Record<string, number>;
  imported?: number;
  attachments?: number;
};

type Job = {
  id: string;
  status: "ready" | "running" | "done" | "failed";
  stats: Stats;
  error: string | null;
};

export default function Recovery() {
  const [dump, setDump] = useState<File | null>(null);
  const [archive, setArchive] = useState<File | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const prepare = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("dump", dump!);
      form.append("export", archive!);
      return api<Job>("/api/recovery", { method: "POST", body: form });
    },
    onSuccess: setJob,
  });
  const status = useQuery({
    queryKey: ["recovery", job?.id],
    queryFn: () => api<Job>(`/api/recovery/${job!.id}`),
    enabled: job?.status === "running",
    refetchInterval: query => !query.state.data || query.state.data.status === "running" ? 2000 : false,
  });
  const execute = useMutation({
    mutationFn: () => api<Job>(`/api/recovery/${job!.id}/execute`, { method: "POST" }),
    onSuccess: setJob,
  });
  const current = status.data ?? job;
  const stats = current?.stats;
  const running = current?.status === "running";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (dump && archive) prepare.mutate();
  };

  return <div className="page">
    <h1>Импорт истории</h1>
    <p>Восстановление старых диалогов из SQL-дампа и Telegram-экспорта. Сообщения клиентам не отправляются.</p>
    <Card className="card">
      <form className="form" onSubmit={submit}>
        <label>SQL-дамп со старыми users и relay_map
          <Input type="file" accept=".sql" onChange={event => setDump(event.target.files?.[0] ?? null)} />
        </label>
        <label>ZIP с messages.html и каталогами вложений
          <Input type="file" accept=".zip" onChange={event => setArchive(event.target.files?.[0] ?? null)} />
        </label>
        <p className="muted">SQL не выполняется как код: импортёр читает только данные COPY. ZIP проверяется на небезопасные пути и превышение размера.</p>
        {prepare.isError && <p className="error">{prepare.error.message}</p>}
        <Button type="submit" disabled={!dump || !archive || prepare.isPending || running}>
          {prepare.isPending ? "Загружаем и проверяем…" : "Загрузить и проверить"}
        </Button>
      </form>
    </Card>
    {current && stats && <Card className="card">
      <h2>Результат проверки</h2>
      <p>Найдено <b>{stats.messages}</b> сообщений для <b>{stats.clients}</b> клиентов.</p>
      <p>Клиент: {stats.directions.client ?? 0} · Поддержка: {stats.directions.staff ?? 0} · Системные: {stats.directions.system ?? 0}</p>
      <p>Вложения: {stats.media} · Кнопки: {stats.buttons} · Связи с заказами: {stats.orders} · Пропущено без привязки: {stats.skipped.unmapped ?? 0}</p>
      <p>Заказов в дампе: {stats.orders_in_dump}. Отсутствующие в текущей БД будут восстановлены без перезаписи существующих.</p>
      {stats.orders_only_in_export > 0 && <p className="error">В экспорте есть ID заказов, которых нет в дампе: {stats.orders_only_in_export}. Они не будут выдуманы автоматически.</p>}
      <details>
        <summary>Покрытие всех топиков ({stats.topics.length})</summary>
        <div className="sup-search-results">
          <table><thead><tr><th>Клиент ID</th><th>Сообщения</th><th>Кнопки</th><th>Файлы</th><th>Заказы</th></tr></thead>
            <tbody>{stats.topics.map(topic => <tr key={topic.user_id}><td>{topic.user_id}</td><td>{topic.messages}</td><td>{topic.buttons}</td><td>{topic.media}</td><td>{topic.orders}</td></tr>)}</tbody>
          </table>
        </div>
      </details>
      {stats.media_missing > 0 && <p className="error">В ZIP отсутствуют файлы: {stats.media_missing}. Импорт остановлен, чтобы история не была неполной.</p>}
      {current.status === "ready" && <p className="muted">Это только отчёт. В базу пока ничего не записано.</p>}
      {running && <p role="status">Импорт выполняется. Не закрывайте сервер; страницу можно обновить.</p>}
      {current.status === "done" && <p className="ok">Импорт завершён: добавлено {stats.imported ?? 0} сообщений, {stats.attachments ?? 0} вложений и {stats.orders_backfilled ?? 0} заказов. Загруженные исходники удалены.</p>}
      {current.status === "failed" && <p className="error">{current.error || "Импорт не выполнен"}. Файлы сохранены для повторной попытки.</p>}
      {execute.isError && <p className="error">{execute.error.message}</p>}
      <Button disabled={running || current.status === "done" || stats.media_missing > 0 || !stats.messages || execute.isPending} onClick={() => execute.mutate()}>
        {current.status === "failed" ? "Повторить импорт" : "Импортировать в базу"}
      </Button>
    </Card>}
  </div>;
}
