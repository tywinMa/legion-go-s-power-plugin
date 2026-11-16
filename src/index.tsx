import { callable, definePlugin } from "@decky/api";
import { ButtonItem, PanelSection, PanelSectionRow, SliderField, ToggleField, staticClasses } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { FaBolt } from "react-icons/fa";
import { version } from "../package.json";

type Limits = { battery: number; ac: number };
type State = {
  available: boolean;
  enabled: boolean;
  active: boolean;
  error: string | null;
  canRestore: boolean;
  source: keyof Limits | null;
  limits: [number, number, number] | null;
  power: number | null;
  target: number | null;
  profile?: string;
  minimum?: number;
  maximum?: number;
  globalLimits?: Limits;
};

const getState = callable<[], State>("get_state");
const saveLimits = callable<[number, number], State>("save_limits");
const setEnabled = callable<[boolean], State>("set_enabled");

const profileLabels: Record<string, string> = {
  "low-power": "低功耗", balanced: "均衡", performance: "性能", custom: "自定义",
};

function Content() {
  const [state, setState] = useState<State>();
  const [draft, setDraft] = useState<Limits>();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const revision = useRef(0);

  useEffect(() => {
    let alive = true;
    let pending = false;
    const refresh = async () => {
      if (pending || revision.current % 2) return;
      const started = revision.current;
      pending = true;
      try {
        const next = await getState();
        if (alive && started === revision.current) setState(next);
      } catch (error) {
        if (alive && started === revision.current) setMessage(String(error));
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => { alive = false; clearInterval(timer); };
  }, []);

  const act = async (action: () => Promise<State>, saving = false) => {
    if (revision.current % 2) return;
    revision.current++;
    setBusy(true);
    setMessage("");
    try {
      const next = await action();
      setState(next);
      if (saving) {
        setDraft(undefined);
        if (!next.error) setMessage("已保存全局瓦数。");
      }
    } catch (error) {
      setMessage(String(error));
    } finally {
      revision.current++;
      setBusy(false);
    }
  };

  const values = draft ?? state?.globalLimits;
  let status = "正在读取设备…";
  if (state?.active) status = `限制生效 · ${state.target} W`;
  else if (state?.enabled) status = "功率限制待应用";
  else if (state?.canRestore) status = "尚未交回系统控制";
  else if (state) status = "系统控制";

  return <PanelSection title="APU 功率">
    <PanelSectionRow>
      <ToggleField label="全局功率限制" checked={state?.enabled ?? false} disabled={busy || !state?.available}
        onChange={enabled => void act(() => setEnabled(enabled))} />
    </PanelSectionRow>
    <PanelSectionRow>
      <div>{status}</div>
      {state?.profile && <div style={{ marginTop: 5, fontSize: 12 }}>
        {state.source === "ac" ? "插电" : state.source === "battery" ? "电池" : "供电未知"}
        {" · "}{profileLabels[state.profile] ?? state.profile}档
        {state.power != null && ` · 实时 ${state.power.toFixed(1)} W`}
      </div>}
      {state?.limits && <div style={{ marginTop: 5, fontSize: 12 }}>
        持续 / 短时 / 瞬时上限 {state.limits.join(" / ")} W
      </div>}
    </PanelSectionRow>
    {state?.available && values && <>
      <PanelSectionRow>
        <SliderField label="电池功率上限" min={state.minimum} max={state.maximum}
          step={1} showValue valueSuffix=" W" value={values.battery} disabled={busy}
          onChange={battery => setDraft({ ...values, battery })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <SliderField label="插电功率上限" min={state.minimum} max={state.maximum}
          step={1} showValue valueSuffix=" W" value={values.ac} disabled={busy}
          onChange={ac => setDraft({ ...values, ac })} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy || !draft}
          onClick={() => void act(() => saveLimits(values.battery, values.ac), true)}>
          {state.enabled ? "保存并应用" : "保存瓦数"}
        </ButtonItem>
      </PanelSectionRow>
      {!state.enabled && state.canRestore && <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => void act(() => setEnabled(false))}>
          重试交回系统控制
        </ButtonItem>
      </PanelSectionRow>}
    </>}
    {(state?.error || message) && <PanelSectionRow><div role="status">{message || state?.error}</div></PanelSectionRow>}
    <PanelSectionRow><div style={{ opacity: 0.75, fontSize: 12, textAlign: "center" }}>v{version}</div></PanelSectionRow>
  </PanelSection>;
}

export default definePlugin(() => ({
  name: "Legion Go S Power",
  titleView: <div className={staticClasses.Title}>Legion Go S 功率限制</div>,
  content: <Content />,
  icon: <FaBolt />,
}));
