import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { api, json } from "../api";
import { SkeletonText } from "../Skeletons";
import { toast } from "../Toaster";

type Config = {
  shop_id: string;
  hash_algorithm: string;
  test: boolean;
  password1_set: boolean;
  password2_set: boolean;
  configured: boolean;
  encryption_configured: boolean;
  source: "database" | "unset";
  updated_at: string | null;
};

type FormState = {
  shop_id: string;
  password1: string;
  password2: string;
  hash_algorithm: string;
  test: boolean;
};

export default function Robokassa() {
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["robokassa"], queryFn: () => api<Config>("/api/robokassa") });
  const d = config.data;
  const [f, setF] = useState<FormState>({ shop_id: "", password1: "", password2: "", hash_algorithm: "MD5", test: false });
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (d && !loaded) {
      setF({ shop_id: d.shop_id, password1: "", password2: "", hash_algorithm: d.hash_algorithm, test: d.test });
      setLoaded(true);
    }
  }, [d, loaded]);
  const save = useMutation({
    mutationFn: () => api<Config>("/api/robokassa", json("PUT", f)),
    onSuccess: (result) => {
      setF(current => ({ ...current, password1: "", password2: "" }));
      qc.setQueryData(["robokassa"], result);
      toast("Настройки Robokassa сохранены. Новые счёта будут использовать их сразу");
    },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  const origin = window.location.origin;
  const needsPasswords = !d || ((!d.password1_set && !f.password1) || (!d.password2_set && !f.password2));

  return <div className="page">
    <h1>Robokassa</h1>
    <p>Общие настройки оплаты для всех обложек. Менять их в Portainer не нужно: бот читает актуальную запись из PostgreSQL при создании счёта и проверке оплаты.</p>
    <Card className="card">
      {config.isLoading ? <SkeletonText lines={3} /> : config.isError || !d ? <p className="error">Не удалось загрузить настройки.</p> : <>
        <p>
          <span className={`badge ${d.configured ? "ok" : "off"}`}>{d.configured ? "Настроено" : "Не настроено"}</span>{" "}
          <span className="muted">Источник: {d.source === "database" ? "настройки панели" : "ещё не заданы"}</span>
        </p>
        {!d.encryption_configured && <p className="error">Для сохранения паролей задайте одинаковый WEB_SESSION_SECRET в процессах bot и site.</p>}
        <form className="form" onSubmit={submit}>
          <label>Идентификатор магазина (MerchantLogin)
            <Input value={f.shop_id} onChange={e => setF({ ...f, shop_id: e.target.value })} maxLength={64} autoComplete="off" required />
          </label>
          <label>Пароль №1 {d.password1_set && <span className="muted">— сохранён, пусто означает не менять</span>}
            <Input type="password" value={f.password1} onChange={e => setF({ ...f, password1: e.target.value })} maxLength={256} autoComplete="new-password" placeholder={d.password1_set ? "Новый пароль №1" : "Пароль №1"} />
          </label>
          <label>Пароль №2 {d.password2_set && <span className="muted">— сохранён, пусто означает не менять</span>}
            <Input type="password" value={f.password2} onChange={e => setF({ ...f, password2: e.target.value })} maxLength={256} autoComplete="new-password" placeholder={d.password2_set ? "Новый пароль №2" : "Пароль №2"} />
          </label>
          <label>Алгоритм расчёта хэша
            <Select value={f.hash_algorithm} onValueChange={value => setF({ ...f, hash_algorithm: value })}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>{["MD5", "SHA1", "SHA256", "SHA512"].map(algorithm => <SelectItem key={algorithm} value={algorithm}>{algorithm}</SelectItem>)}</SelectContent>
            </Select>
          </label>
          <label className="row-actions">
            <Switch checked={f.test} onCheckedChange={checked => setF({ ...f, test: checked })} />
            <span>Тестовый режим (IsTest=1)</span>
          </label>
          <p className="muted">{f.test ? "Для тестового режима нужны отдельные тестовые пароли из кабинета Robokassa." : "Для боевого режима используются боевые пароли магазина."}</p>
          {save.isError && <p className="error">{save.error.message}</p>}
          <Button type="submit" disabled={!f.shop_id.trim() || needsPasswords || !d.encryption_configured || save.isPending}>
            {save.isPending ? "Сохраняем…" : "Сохранить настройки"}
          </Button>
        </form>
      </>}
    </Card>
    <Card className="card">
      <h2>Технические настройки в кабинете Robokassa</h2>
      <p>Result URL (POST):</p><pre className="details">{origin}/robokassa/result</pre>
      <p>Success URL (GET):</p><pre className="details">{origin}/robokassa/success</pre>
      <p>Fail URL (GET):</p><pre className="details">{origin}/robokassa/fail</pre>
      <p className="muted">Эти пути должны проксироваться в bot:8080. После смены магазина, паролей или режима перевыставьте счета: старые ссылки сохраняют прежнюю подпись. Ошибка 29 означает неверную подпись — чаще всего перепутаны тестовые и боевые пароли.</p>
    </Card>
  </div>;
}
