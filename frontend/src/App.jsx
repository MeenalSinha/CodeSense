import { useState, useEffect, useRef, useCallback } from "react";

// ─── CONFIG ───────────────────────────────────────────────────────────────────
const API_BASE = "http://localhost:8000";
const WS_URL   = "ws://localhost:8000/ws";

// ─── DESIGN TOKENS ────────────────────────────────────────────────────────────
const C = {
  bg: "#F5F6F8", surface: "#FFFFFF", surfaceHigh: "#F0F2F5",
  border: "#DDE1E9", borderBright: "#C5CBD8",
  accent: "#2563EB", accentGlow: "#2563EB18", accentSoft: "#EEF2FF",
  green: "#16A34A", greenSoft: "#F0FDF4",
  yellow: "#B45309", yellowSoft: "#FFFBEB",
  red: "#DC2626", redSoft: "#FEF2F2",
  purple: "#7C3AED",
  primary: "#111827", secondary: "#374151", muted: "#9CA3AF",
};
const MONO = "'JetBrains Mono','Fira Code',monospace";
const SANS = "'DM Sans','Outfit',system-ui,sans-serif";

// ─── API LAYER ────────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

const api = {
  health: () => apiFetch("/api/health"),
  analyze: (code, language = "python") =>
    apiFetch("/api/analyze", { method:"POST", body:JSON.stringify({ code, language }) }),
  execute: (code, stdin = "") =>
    apiFetch("/api/execute", { method:"POST", body:JSON.stringify({ code, stdin, timeout:5 }) }),
  mentor: {
    chat: (message, history, current_code, current_error, language) =>
      apiFetch("/api/mentor/chat", { method:"POST", body:JSON.stringify({ message, history, current_code, current_error, language }) }),
    hint: (problem_id, hint_level, current_code, language) =>
      apiFetch("/api/mentor/hint", { method:"POST", body:JSON.stringify({ problem_id, hint_level, current_code, language }) }),
  },
  practice: {
    list: (category) => {
      const p = new URLSearchParams();
      if (category && category !== "All") p.set("category", category);
      return apiFetch(`/api/practice/problems?${p}`);
    },
    get: (id) => apiFetch(`/api/practice/problems/${id}`),
    submit: (problem_id, code) =>
      apiFetch("/api/practice/submit", { method:"POST", body:JSON.stringify({ problem_id, code }) }),
  },
};

// ─── LOCAL FALLBACKS (backend offline) ───────────────────────────────────────
function localAnalyze(code) {
  if (!code.trim()) return { nodes:[], edges:[], concepts:[], plain_english:"", why_this_works:"" };
  const nodes = [], edges = [];
  let id = 0;
  const mk = (type, label, detail="", color=C.accent) => { const n={id:`n${id++}`,type,label,detail,color}; nodes.push(n); return n.id; };
  const edge = (from,to,label="") => edges.push({from,to,label});
  const concepts = new Set();
  const start = mk("start","START","Program begins",C.green);
  let prev = start;
  for (const raw of code.split("\n")) {
    const t = raw.trim();
    if (!t || t.startsWith("#")) continue;
    if (t.startsWith("def "))    { concepts.add("functions");  const n=mk("function_def",t.replace(/\s*:.*/,"").substring(0,35),"",C.purple); edge(prev,n); prev=n; }
    else if (t.startsWith("class ")) { concepts.add("classes"); const n=mk("class_def",t.replace(/\s*:.*/,"").substring(0,35),"",C.purple); edge(prev,n); prev=n; }
    else if (t.startsWith("for ")||t.startsWith("while ")) { concepts.add("loops"); const n=mk("loop",t.replace(/\s*:.*/,"").substring(0,35),"Iterates",C.yellow); edge(prev,n); edge(n,n,"repeat"); prev=n; }
    else if (t.startsWith("if ")) { concepts.add("conditions"); const n=mk("condition",t.replace(/\s*:.*/,"").substring(0,35),"Branch",C.yellow); edge(prev,n); prev=n; }
    else if (t.startsWith("return ")) { const n=mk("return",t.substring(0,35),"",C.purple); edge(prev,n); prev=n; }
    else if (t.startsWith("print(")) { concepts.add("output"); const n=mk("output",t.substring(0,35),"stdout",C.accent); edge(prev,n); prev=n; }
    else if (t.includes("=")&&!t.includes("==")) { concepts.add("variables"); const n=mk("assign",t.substring(0,35),"",C.accent); edge(prev,n); prev=n; }
    else if (t.startsWith("import")||t.startsWith("from ")) { concepts.add("imports"); const n=mk("import",t.substring(0,35),"",C.secondary); edge(prev,n); prev=n; }
  }
  const end=mk("end","END","Program complete",C.red); edge(prev,end);
  const cArr=[...concepts];
  return {
    nodes, edges, concepts:cArr,
    plain_english: cArr.length ? `This code ${cArr.join(", ")}.` : "Sequential Python statements.",
    why_this_works: cArr.includes("functions") ? "Functions package reusable logic — write once, call many times." : cArr.includes("loops") ? "Loops automate repetition — write once, repeat N times." : "Python executes top-to-bottom, one statement at a time.",
    skill_updates: Object.fromEntries(cArr.map(c=>[c,3])),
  };
}

function localMentorReply(msg) {
  const q = msg.toLowerCase();
  if (q.includes("recursion")) return "Every recursive function needs a base case — what makes yours stop? 🌀";
  if (q.includes("loop"))      return "What changes on each iteration? That variable captures the change. 🔄";
  if (q.includes("error")||q.includes("bug")) return "What did you *expect* vs what *actually* happened? That gap is the bug. 🔍";
  if (q.includes("return"))    return "A function is a vending machine — `return` is what it gives back. 🎰";
  if (q.includes("variable"))  return "Variables are labelled boxes 📦. When you reassign, the old value is replaced.";
  if (q.includes("hint")||q.includes("stuck")) return "What's the *simplest* version of this problem you could solve? Start tiny, then expand. 🪜";
  return "What's your current mental model? Stating your guess — even if wrong — pinpoints exactly where the confusion is. 🤔";
}

// ─── WEBSOCKET ────────────────────────────────────────────────────────────────
class WSClient {
  constructor() { this.ws=null; this.handlers={}; this._rid=0; this.connected=false; }
  connect() {
    return new Promise((res,rej) => {
      try {
        this.ws = new WebSocket(WS_URL);
        this.ws.onopen  = () => { this.connected=true; res(); };
        this.ws.onclose = () => { this.connected=false; };
        this.ws.onerror = () => rej(new Error("WS unavailable"));
        this.ws.onmessage = (e) => {
          try {
            const {type,payload,request_id}=JSON.parse(e.data);
            (this.handlers[type]||[]).forEach(h=>h(payload,request_id));
          } catch {}
        };
      } catch(e) { rej(e); }
    });
  }
  on(type,fn) {
    if (!this.handlers[type]) this.handlers[type]=[];
    this.handlers[type].push(fn);
    return ()=>{ this.handlers[type]=this.handlers[type].filter(h=>h!==fn); };
  }
  send(type,payload={}) {
    const rid=`r${++this._rid}`;
    if (this.ws?.readyState===WebSocket.OPEN)
      this.ws.send(JSON.stringify({type,payload,request_id:rid}));
    return rid;
  }
  analyze(code,language="en") { return this.send("analyze",{code,language}); }
  execute(code)              { return this.send("execute",{code,timeout:5}); }
  mentorChat(msg,history,code,err) { return this.send("mentor_chat",{message:msg,history,current_code:code,current_error:err}); }
}
const ws = new WSClient();

// ─── UI COMPONENTS ────────────────────────────────────────────────────────────

function BackendBadge({status}) {
  const cfg={
    online:  {dot:C.green, label:"Backend online",       bg:C.greenSoft+"66"},
    offline: {dot:C.red,   label:"Offline — local mode", bg:C.redSoft+"66"},
    mock:    {dot:C.yellow,label:"Mock LLM active",      bg:C.yellowSoft+"66"},
    checking:{dot:C.muted, label:"Connecting…",          bg:C.border},
  }[status]||{dot:C.muted,label:status,bg:C.border};
  return (
    <div style={{display:"flex",alignItems:"center",gap:5,padding:"3px 10px",borderRadius:20,background:cfg.bg,border:`1px solid ${cfg.dot}30`}}>
      <div style={{width:6,height:6,borderRadius:"50%",background:cfg.dot,boxShadow:`0 0 6px ${cfg.dot}`}}/>
      <span style={{fontFamily:SANS,fontSize:10,color:cfg.dot,fontWeight:600}}>{cfg.label}</span>
    </div>
  );
}

// ── Node colour map ───────────────────────────────────────────────────────────
const NODE_COLOR = {
  start:"#059669", end:"#DC2626",
  condition:"#D97706", loop:"#D97706",
  function_def:"#7C3AED", class_def:"#7C3AED", return:"#7C3AED",
  output:"#2563EB", assign:"#2563EB", statement:"#2563EB",
  import:"#64748B", exception:"#DC2626",
  branch_true:"#059669", branch_false:"#DC2626",
};
const NODE_TYPE_LABEL = {
  start:"START", end:"END", condition:"IF", loop:"LOOP",
  function_def:"DEF", class_def:"CLASS", return:"RETURN",
  output:"PRINT", assign:"ASSIGN", import:"IMPORT",
  exception:"ERROR", statement:"CODE",
};

function Flowchart({nodes,edges,loading}) {
  if (loading) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:10,padding:"24px 16px"}}>
      <style>{`@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}`}</style>
      {[180,220,200,160].map((w,i)=>(
        <div key={i} style={{height:44,borderRadius:8,width:w,
          background:"linear-gradient(90deg,#E5E7EB 25%,#F9FAFB 50%,#E5E7EB 75%)",
          backgroundSize:"400% 100%",animation:`shimmer 1.6s ${i*0.15}s infinite`}}/>
      ))}
      <p style={{fontFamily:SANS,fontSize:12,color:C.muted,margin:"4px 0 0"}}>Analysing code…</p>
    </div>
  );

  if (!nodes.length) return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",
      height:"100%",gap:10,color:C.muted,padding:24}}>
      <svg width={40} height={40} viewBox="0 0 24 24" fill="none" stroke={C.border} strokeWidth={1.5}>
        <rect x={3} y={3} width={18} height={18} rx={3}/>
        <path d="M3 9h18M9 21V9"/>
      </svg>
      <span style={{fontFamily:SANS,fontSize:13,color:C.secondary}}>Write code to see the flow</span>
    </div>
  );

  // ── Layout constants ───────────────────────────────────────────────────────
  const NW = 220, NH = 48, GAP = 52, PAD = 20;
  const SVG_W = NW + PAD*2 + 80; // extra right room for loop arcs
  const NX = PAD + 40;           // x offset for all nodes (centred)

  const positioned = nodes.map((n,i) => ({
    ...n,
    x: NX,
    y: PAD + i*(NH+GAP),
  }));
  const nMap = Object.fromEntries(positioned.map(n=>[n.id,n]));
  const totalH = PAD + positioned.length*(NH+GAP) + PAD;

  // ── Node renderer ─────────────────────────────────────────────────────────
  const renderNode = n => {
    const col   = NODE_COLOR[n.type] || "#2563EB";
    const badge = NODE_TYPE_LABEL[n.type] || n.type.toUpperCase();
    const isTerminal = n.type==="start" || n.type==="end";
    const isDiamond  = n.type==="condition" || n.type==="loop";

    // Truncate label cleanly
    const rawLbl = String(n.label||"").replace(/^(def |class |for |while |if )/,"").trim();
    const lbl = rawLbl.length > 26 ? rawLbl.slice(0,24)+"…" : rawLbl;

    if (isDiamond) {
      // Diamond — taller for readability
      const DH = NH+10, DW = NW;
      const cx=n.x+DW/2, cy=n.y+DH/2;
      const px=DW*0.45, py=DH*0.48;
      return (
        <g key={n.id}>
          <polygon
            points={`${cx},${cy-py} ${cx+px},${cy} ${cx},${cy+py} ${cx-px},${cy}`}
            fill="#FFFBEB" stroke={col} strokeWidth={2}
          />
          <text x={cx} y={cy+1} textAnchor="middle" dominantBaseline="middle"
            fill={col} fontSize={10} fontWeight="700" fontFamily={MONO}>{lbl}</text>
        </g>
      );
    }

    if (isTerminal) {
      // Pill shape, solid fill
      return (
        <g key={n.id}>
          <rect x={n.x} y={n.y} width={NW} height={NH} rx={NH/2}
            fill={col} stroke="none"/>
          <text x={n.x+NW/2} y={n.y+NH/2+1} textAnchor="middle" dominantBaseline="middle"
            fill="#fff" fontSize={13} fontWeight="800" fontFamily={SANS} letterSpacing="1">{badge}</text>
        </g>
      );
    }

    // Regular node — white card with left colour strip + badge pill
    const badgeW = badge.length*7+10;
    return (
      <g key={n.id}>
        {/* Card */}
        <rect x={n.x} y={n.y} width={NW} height={NH} rx={8}
          fill="#fff" stroke={C.border} strokeWidth={1.5}/>
        {/* Left colour strip */}
        <rect x={n.x} y={n.y+6} width={4} height={NH-12} rx={2} fill={col}/>
        {/* Type badge */}
        <rect x={n.x+12} y={n.y+NH/2-9} width={badgeW} height={18} rx={9}
          fill={col+"18"} stroke={col} strokeWidth={1}/>
        <text x={n.x+12+badgeW/2} y={n.y+NH/2+1} textAnchor="middle" dominantBaseline="middle"
          fill={col} fontSize={8} fontWeight="700" fontFamily={SANS}>{badge}</text>
        {/* Label */}
        <text x={n.x+18+badgeW} y={n.y+NH/2+1} dominantBaseline="middle"
          fill={C.primary} fontSize={11} fontWeight="500" fontFamily={MONO}>{lbl}</text>
      </g>
    );
  };

  // ── Edge renderer ─────────────────────────────────────────────────────────
  const renderEdge = (e, i) => {
    const fid = e.from_node||e.from, tid = e.to_node||e.to;
    const from = nMap[fid], to = nMap[tid];
    if (!from||!to) return null;

    // Self-loop (loop node back to itself)
    if (fid===tid) {
      const rx = from.x+NW, ry = from.y+NH/2;
      const d = `M${rx} ${from.y+NH*0.3} C${rx+60} ${from.y} ${rx+60} ${from.y+NH} ${rx} ${from.y+NH*0.7}`;
      return (
        <g key={i}>
          <path d={d} fill="none" stroke="#D97706" strokeWidth={1.5}
            strokeDasharray="4 3" markerEnd="url(#fc-loop)"/>
        </g>
      );
    }

    // Diamond nodes are taller — exit from their bottom point
    const fromIsDiamond = from.type==="condition"||from.type==="loop";
    const DH = NH+10;
    const x1 = from.x+NW/2;
    const y1 = fromIsDiamond ? from.y+DH/2+DH*0.48 : from.y+NH;
    const x2 = to.x+NW/2;
    const y2 = to.y;
    const edgeCol = e.label==="True"?"#059669":e.label==="False"?"#DC2626":"#CBD5E1";

    // Straight vertical line with subtle elbow
    const cy = (y1+y2)/2;
    const d = `M${x1} ${y1} C${x1} ${cy} ${x2} ${cy} ${x2} ${y2}`;

    return (
      <g key={i}>
        <path d={d} fill="none" stroke={edgeCol} strokeWidth={1.5}
          markerEnd={`url(#fc-${e.label==="True"?"t":e.label==="False"?"f":"n"})`}/>
        {e.label && e.label !== "" && (
          <text x={(x1+x2)/2+4} y={cy} dominantBaseline="middle"
            fill={edgeCol} fontSize={9} fontWeight="700" fontFamily={SANS}>{e.label}</text>
        )}
      </g>
    );
  };

  return (
    <div style={{overflowY:"auto",overflowX:"auto",height:"100%",background:C.surface}}>
      <svg width={SVG_W} height={totalH} style={{display:"block"}}>
        <defs>
          <marker id="fc-n" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#CBD5E1"/>
          </marker>
          <marker id="fc-t" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#059669"/>
          </marker>
          <marker id="fc-f" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#DC2626"/>
          </marker>
          <marker id="fc-loop" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#D97706"/>
          </marker>
        </defs>

        {edges.map(renderEdge)}
        {positioned.map(renderNode)}
      </svg>
    </div>
  );
}

function SkillMeter({skills}) {
  const defs=[
    ["variables","Variables","📦"],["loops","Loops","🔄"],["conditions","Conditions","🔀"],
    ["functions","Functions","⚡"],["classes","Classes","🏗️"],["recursion","Recursion","🌀"],
    ["exceptions","Exceptions","🛡️"],["imports","Imports","📥"],
  ];
  return (
    <div style={{display:"flex",flexDirection:"column",gap:14}}>
      {defs.map(([key,label,icon])=>{
        const pct=Math.min(100,skills[key]||0);
        const col=pct>70?C.green:pct>40?C.yellow:C.accent;
        return (
          <div key={key}>
            <div style={{display:"flex",justifyContent:"space-between",marginBottom:5}}>
              <span style={{fontFamily:SANS,fontSize:12,color:C.secondary}}>{icon} {label}</span>
              <span style={{fontFamily:MONO,fontSize:11,color:col}}>{pct}%</span>
            </div>
            <div style={{background:C.border,borderRadius:4,height:6,overflow:"hidden"}}>
              <div style={{width:`${pct}%`,height:"100%",background:col,borderRadius:4,transition:"width 0.8s cubic-bezier(.4,0,.2,1)",boxShadow:`0 0 8px ${col}60`}}/>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MentorChat({currentCode,currentError,backendOnline,hindiMode}) {
  const [msgs,setMsgs]=useState([{role:"mentor",text:"👋 Hi! I'm your CodeSense mentor. I give hints, never answers. What are you trying to understand?"}]);
  const [input,setInput]=useState("");
  const [thinking,setThinking]=useState(false);
  const histRef=useRef([]);
  const bottomRef=useRef(null);

  useEffect(()=>{ bottomRef.current?.scrollIntoView({behavior:"smooth"}); },[msgs]);

  const send=useCallback(async()=>{
    if (!input.trim()||thinking) return;
    const text=input.trim(); setInput("");
    const uMsg={role:"user",text};
    setMsgs(prev=>[...prev,uMsg]);
    histRef.current.push({role:"user",content:text});
    setThinking(true);
    try {
      let reply;
      if (backendOnline) {
        const res=await api.mentor.chat(text,histRef.current.slice(-10),currentCode,currentError,hindiMode?"hi":"en");
        reply=res.reply;
      } else {
        await new Promise(r=>setTimeout(r,600+Math.random()*400));
        reply=localMentorReply(text);
      }
      histRef.current.push({role:"assistant",content:reply});
      setMsgs(prev=>[...prev,{role:"mentor",text:reply}]);
    } catch {
      const fb=localMentorReply(text);
      setMsgs(prev=>[...prev,{role:"mentor",text:fb}]);
    } finally { setThinking(false); }
  },[input,thinking,currentCode,currentError,backendOnline,hindiMode]);

  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%"}}>
      <div style={{flex:1,overflowY:"auto",padding:12,display:"flex",flexDirection:"column",gap:10}}>
        {msgs.map((m,i)=>(
          <div key={i} style={{display:"flex",flexDirection:m.role==="user"?"row-reverse":"row",gap:8,alignItems:"flex-start"}}>
            {m.role==="mentor" && <div style={{width:28,height:28,borderRadius:"50%",background:C.accentGlow,border:`1px solid ${C.accent}`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:14,flexShrink:0}}>🧑‍🏫</div>}
            <div style={{maxWidth:"82%",padding:"9px 12px",borderRadius:m.role==="user"?"12px 4px 12px 12px":"4px 12px 12px 12px",background:m.role==="user"?C.accent+"30":C.surfaceHigh,border:`1px solid ${m.role==="user"?C.accent+"50":C.border}`,fontFamily:SANS,fontSize:13,color:C.primary,lineHeight:1.5}}>
              {m.text}
            </div>
          </div>
        ))}
        {thinking && (
          <div style={{display:"flex",gap:8,alignItems:"center"}}>
            <div style={{width:28,height:28,borderRadius:"50%",background:C.accentGlow,border:`1px solid ${C.accent}`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:14}}>🧑‍🏫</div>
            <div style={{padding:"9px 14px",borderRadius:"4px 12px 12px 12px",background:C.surfaceHigh,border:`1px solid ${C.border}`,display:"flex",gap:5}}>
              {[0,1,2].map(i=><div key={i} style={{width:6,height:6,borderRadius:"50%",background:C.accent,animation:`pulse 1.2s ${i*0.2}s infinite`}}/>)}
            </div>
          </div>
        )}
        <div ref={bottomRef}/>
      </div>
      <div style={{padding:"10px 12px",borderTop:`1px solid ${C.border}`,display:"flex",gap:8}}>
        <input value={input} onChange={e=>setInput(e.target.value)} onKeyDown={e=>e.key==="Enter"&&send()}
          placeholder="Ask about your code… (hints only, never answers)"
          style={{flex:1,background:C.surfaceHigh,border:`1px solid ${C.border}`,borderRadius:8,padding:"8px 12px",color:C.primary,fontFamily:SANS,fontSize:13,outline:"none",transition:"border-color 0.15s"}}/>
        <button onClick={send} disabled={!input.trim()||thinking}
          style={{background:C.accent,border:"none",borderRadius:8,padding:"8px 14px",color:"#fff",cursor:"pointer",fontFamily:SANS,fontSize:13,fontWeight:600,opacity:(!input.trim()||thinking)?0.5:1}}>
          Ask
        </button>
      </div>
    </div>
  );
}

function CodeEditor({value,onChange}) {
  const ref=useRef(null);
  const onTab=(e)=>{
    if (e.key!=="Tab") return;
    e.preventDefault();
    const s=ref.current,st=s.selectionStart,en=s.selectionEnd;
    onChange(value.substring(0,st)+"    "+value.substring(en));
    setTimeout(()=>{s.selectionStart=s.selectionEnd=st+4;},0);
  };
  return (
    <textarea ref={ref} value={value} onChange={e=>onChange(e.target.value)} onKeyDown={onTab} spellCheck={false}
      style={{width:"100%",height:"100%",background:"transparent",border:"none",outline:"none",fontFamily:MONO,fontSize:14,color:C.primary,lineHeight:1.7,resize:"none",padding:"16px",boxSizing:"border-box",caretColor:C.accent}}/>
  );
}

function AntiVibeOverlay({onPredict,prediction,setPrediction,checking,result,onDismiss}) {
  return (
    <div style={{position:"absolute",inset:0,background:"rgba(245,246,248,0.95)",backdropFilter:"blur(6px)",display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",zIndex:100,gap:16}}>
      <div style={{fontSize:42}}>🛑</div>
      <h3 style={{fontFamily:SANS,color:C.primary,margin:0,fontSize:20,fontWeight:800}}>Anti-Vibe Coding Mode</h3>
      <p style={{fontFamily:SANS,color:C.secondary,margin:0,textAlign:"center",maxWidth:360,fontSize:14,lineHeight:1.6}}>
        Before running, <strong style={{color:C.yellow}}>predict the exact output.</strong><br/>
        This trains your mental model — no peeking!
      </p>
      {result===null ? (
        <>
          <textarea value={prediction} onChange={e=>setPrediction(e.target.value)}
            placeholder="Type your predicted output here…"
            style={{width:360,height:100,background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:12,color:C.primary,fontFamily:MONO,fontSize:13,resize:"none",outline:"none",boxShadow:"0 1px 4px rgba(0,0,0,0.08)"}}/>
          <button onClick={onPredict} disabled={!prediction.trim()||checking}
            style={{background:C.accent,border:"none",borderRadius:10,padding:"10px 28px",color:"#fff",cursor:"pointer",fontFamily:SANS,fontSize:14,fontWeight:700,opacity:!prediction.trim()?0.5:1}}>
            {checking?"Checking…":"Lock In & Run →"}
          </button>
        </>
      ) : (
        <div style={{width:380,padding:20,borderRadius:14,background:result.correct?C.greenSoft:C.redSoft,border:`1px solid ${result.correct?C.green+"40":C.red+"40"}`,textAlign:"center",boxShadow:"0 2px 12px rgba(0,0,0,0.08)"}}>
          <div style={{fontSize:36,marginBottom:8}}>{result.correct?"🎯":"🤔"}</div>
          <div style={{fontFamily:SANS,color:result.correct?C.green:C.red,fontWeight:800,fontSize:15,marginBottom:10}}>
            {result.correct?"Perfect prediction!":"Not quite — good learning moment!"}
          </div>
          {!result.correct && (
            <div style={{textAlign:"left",fontFamily:MONO,fontSize:11,lineHeight:1.8,marginBottom:10,background:C.surface,padding:"8px 10px",borderRadius:6}}>
              <div style={{color:C.green}}>Expected: {result.actual.substring(0,80)}</div>
              <div style={{color:C.red}}>Predicted: {result.prediction.substring(0,80)}</div>
            </div>
          )}
          <div style={{fontFamily:SANS,color:C.secondary,fontSize:12,marginBottom:14}}>
            {result.correct ? "Your mental model is strong. Keep it up!" : "Trace through your code line by line — where did the logic diverge?"}
          </div>
          <button onClick={onDismiss} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:"7px 18px",color:C.secondary,cursor:"pointer",fontFamily:SANS,fontSize:12}}>
            Close & Continue
          </button>
        </div>
      )}
    </div>
  );
}

// ─── PRACTICE PAGE ────────────────────────────────────────────────────────────
const FALLBACK_PROBLEMS = [
  {id:1,title:"Swap Without Temp",category:"Variables",difficulty:"easy",description:"Swap a=5 and b=10 without using a temp variable. Python has a one-liner for this.",starter_code:"a = 5\nb = 10\n# Swap here\nprint(f'a = {a}, b = {b}')",hint_count:3},
  {id:2,title:"FizzBuzz",category:"Conditions",difficulty:"easy",description:"Print 1-20: Fizz for ×3, Buzz for ×5, FizzBuzz for both.",starter_code:"for i in range(1, 21):\n    pass",hint_count:3},
  {id:3,title:"Recursive Sum",category:"Functions",difficulty:"medium",description:"Write sum_list(lst) using recursion — no built-in sum() allowed.",starter_code:"def sum_list(lst):\n    pass\n\nprint(sum_list([1, 2, 3, 4, 5]))",hint_count:3},
  {id:4,title:"Grade Classifier",category:"Functions",difficulty:"easy",description:"Return A/B/C/D/F based on score using if/elif/else.",starter_code:"def grade(score):\n    pass\n\nprint(grade(95))\nprint(grade(72))\nprint(grade(45))",hint_count:3},
  {id:5,title:"Count Vowels",category:"Loops",difficulty:"easy",description:"Count vowels (a,e,i,o,u upper+lower) in a string.",starter_code:"def count_vowels(text):\n    pass\n\nprint(count_vowels('Hello World'))",hint_count:3},
];

function PracticePage({skills,setSkills,backendOnline,hindiMode}) {
  const [problems,setProblems]=useState([]);
  const [selected,setSelected]=useState(null);
  const [code,setCode]=useState("");
  const [output,setOutput]=useState("");
  const [outputErr,setOutputErr]=useState(false);
  const [running,setRunning]=useState(false);
  const [hintText,setHintText]=useState("");
  const [hintLevel,setHintLevel]=useState(0);
  const [hintLoading,setHintLoading]=useState(false);
  const [filter,setFilter]=useState("All");
  const [solved,setSolved]=useState({});
  const [submitResult,setSubmitResult]=useState(null);
  const [submitting,setSubmitting]=useState(false);

  useEffect(()=>{
    if (backendOnline) {
      api.practice.list().then(r=>setProblems(r.problems||[])).catch(()=>setProblems(FALLBACK_PROBLEMS));
    } else {
      setProblems(FALLBACK_PROBLEMS);
    }
  },[backendOnline]);

  const categories=["All",...new Set(problems.map(p=>p.category))];
  const filtered=filter==="All"?problems:problems.filter(p=>p.category===filter);

  const selectProblem=async(prob)=>{
    setSelected(prob); setOutput(""); setOutputErr(false); setHintText(""); setHintLevel(0); setSubmitResult(null);
    if (backendOnline) {
      try { const full=await api.practice.get(prob.id); setCode(full.starter_code||""); }
      catch { setCode(prob.starter_code||""); }
    } else { setCode(prob.starter_code||""); }
  };

  const runCode=async()=>{
    if (!code.trim()||running) return;
    setRunning(true); setOutput(""); setOutputErr(false); setSubmitResult(null);
    try {
      if (backendOnline) {
        const res=await api.execute(code);
        if (res.status==="success") { setOutput(res.stdout||"(no output)"); setOutputErr(false); }
        else { setOutput(res.stderr||`Status: ${res.status}`); setOutputErr(true); }
      } else {
        setOutput("⚠️ Backend offline — cannot execute code."); setOutputErr(true);
      }
    } catch(e) { setOutput("Error: "+e.message); setOutputErr(true); }
    finally { setRunning(false); }
  };

  const submitSolution=async()=>{
    if (!selected||!code.trim()||submitting) return;
    setSubmitting(true);
    try {
      if (backendOnline) {
        const res=await api.practice.submit(selected.id,code);
        setSubmitResult(res);
        if (res.passed) {
          setSolved(prev=>({...prev,[selected.id]:true}));
          const ns={...skills};
          Object.entries(res.skill_updates||{}).forEach(([k,v])=>{ ns[k]=Math.min(100,(ns[k]||0)+v); });
          setSkills(ns);
        }
      } else {
        setSubmitResult({passed:false,score:0,feedback:"Backend offline — connect backend to submit solutions.",test_results:[]});
      }
    } catch(e){ setSubmitResult({passed:false,score:0,feedback:"Submit error: "+e.message,test_results:[]}); }
    finally { setSubmitting(false); }
  };

  const getHint=async()=>{
    if (!selected||hintLoading) return;
    const next=hintLevel+1;
    if (next>(selected.hint_count||3)) return;
    setHintLoading(true);
    try {
      if (backendOnline) {
        const res=await api.mentor.hint(selected.id,next,code,hindiMode?"hi":"en");
        setHintText(res.hint); setHintLevel(next);
      } else {
        setHintText("Think step by step — what's the simplest version of this problem? Start tiny, then expand. 🪜");
        setHintLevel(next);
      }
    } catch { setHintText("Think about the simplest possible case first."); setHintLevel(next); }
    finally { setHintLoading(false); }
  };

  const diffCol=d=>({easy:C.green,medium:C.yellow,hard:C.red}[d?.toLowerCase()]||C.accent);

  return (
    <div style={{display:"flex",height:"100%"}}>
      {/* Problem list */}
      <div style={{width:280,borderRight:`1px solid ${C.border}`,overflowY:"auto",flexShrink:0,background:C.surface}}>
        <div style={{padding:"14px 16px",borderBottom:`1px solid ${C.border}`}}>
          <div style={{fontFamily:SANS,fontWeight:700,color:C.primary,marginBottom:10,fontSize:14}}>📚 Practice Problems</div>
          <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
            {categories.map(cat=>(
              <button key={cat} onClick={()=>setFilter(cat)} style={{background:filter===cat?C.accent:C.surfaceHigh,border:`1px solid ${filter===cat?C.accent:C.border}`,borderRadius:6,padding:"3px 9px",color:filter===cat?"#fff":C.secondary,cursor:"pointer",fontFamily:SANS,fontSize:11,fontWeight:600}}>{cat}</button>
            ))}
          </div>
        </div>
        {filtered.map(p=>(
          <div key={p.id} onClick={()=>selectProblem(p)} style={{padding:"12px 16px",borderBottom:`1px solid ${C.border}`,cursor:"pointer",background:selected?.id===p.id?C.accentSoft:"transparent",borderLeft:selected?.id===p.id?`3px solid ${C.accent}`:"3px solid transparent",transition:"all 0.15s"}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:3}}>
              <span style={{fontFamily:SANS,fontSize:13,color:C.primary,fontWeight:600}}>{solved[p.id]?"✅ ":""}{p.title}</span>
              <span style={{fontFamily:SANS,fontSize:10,color:diffCol(p.difficulty),fontWeight:700,textTransform:"uppercase"}}>{p.difficulty}</span>
            </div>
            <div style={{fontFamily:SANS,fontSize:11,color:C.muted}}>{p.category}</div>
          </div>
        ))}
      </div>

      {/* Workspace */}
      {selected ? (
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
          <div style={{padding:"14px 20px",borderBottom:`1px solid ${C.border}`,display:"flex",justifyContent:"space-between",alignItems:"flex-start",gap:12}}>
            <div style={{flex:1}}>
              <div style={{fontFamily:SANS,fontWeight:700,color:C.primary,fontSize:16}}>{selected.title}</div>
              <div style={{fontFamily:SANS,color:C.secondary,fontSize:13,marginTop:4,lineHeight:1.6}}>{selected.description}</div>
            </div>
            <div style={{display:"flex",gap:8,flexShrink:0}}>
              <button onClick={getHint} disabled={hintLoading||hintLevel>=(selected.hint_count||3)}
                style={{background:C.yellowSoft,border:`1px solid ${C.yellow}`,borderRadius:8,padding:"6px 14px",color:C.yellow,cursor:"pointer",fontFamily:SANS,fontSize:12,fontWeight:600,opacity:hintLevel>=(selected.hint_count||3)?0.4:1}}>
                {hintLoading?"…":`💡 Hint ${hintLevel}/${selected.hint_count||3}`}
              </button>
              <button onClick={runCode} disabled={running}
                style={{background:C.surfaceHigh,border:`1px solid ${C.borderBright}`,borderRadius:8,padding:"6px 14px",color:running?C.muted:C.primary,cursor:running?"not-allowed":"pointer",fontFamily:SANS,fontSize:12,fontWeight:600}}>
                {running?"⏳":"▶ Run"}
              </button>
              <button onClick={submitSolution} disabled={submitting}
                style={{background:C.accent,border:"none",borderRadius:8,padding:"6px 16px",color:"#fff",cursor:"pointer",fontFamily:SANS,fontSize:12,fontWeight:700}}>
                {submitting?"Checking…":"✓ Submit"}
              </button>
            </div>
          </div>

          {hintText && (
            <div style={{margin:"10px 20px 0",padding:"10px 14px",background:C.yellowSoft,border:`1px solid ${C.yellow}30`,borderRadius:8,display:"flex",justifyContent:"space-between",alignItems:"flex-start",gap:8}}>
              <span style={{fontFamily:SANS,fontSize:12,color:C.yellow,lineHeight:1.5}}>💡 Hint {hintLevel}: {hintText}</span>
              <button onClick={()=>setHintText("")} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",fontSize:14,flexShrink:0}}>✕</button>
            </div>
          )}

          {submitResult && (
            <div style={{margin:"10px 20px 0",padding:"12px 16px",borderRadius:8,background:submitResult.passed?C.greenSoft:C.redSoft,border:`1px solid ${submitResult.passed?C.green:C.red}30`}}>
              <div style={{fontFamily:SANS,fontSize:13,color:submitResult.passed?C.green:C.red,fontWeight:700,marginBottom:4}}>
                {submitResult.passed?`🎉 Passed! Score: ${submitResult.score}/100`:`❌ Score: ${submitResult.score}/100`}
              </div>
              <div style={{fontFamily:SANS,fontSize:12,color:C.secondary,lineHeight:1.5,marginBottom:submitResult.test_results?.length?6:0}}>{submitResult.feedback}</div>
              {submitResult.test_results?.map((r,i)=>(
                <div key={i} style={{fontFamily:MONO,fontSize:11,color:r.passed?C.green:C.red}}>
                  {r.passed?"✅":"❌"} Test {r.test_case} {r.error?`— ${r.error.substring(0,50)}`:r.passed?"passed":`→ expected "${r.expected?.substring(0,30)}"`}
                </div>
              ))}
            </div>
          )}

          <div style={{flex:1,display:"flex",overflow:"hidden",marginTop:10}}>
            <div style={{flex:1,borderRight:`1px solid ${C.border}`,display:"flex",flexDirection:"column"}}>
              <div style={{padding:"6px 16px",background:C.surfaceHigh,borderBottom:`1px solid ${C.border}`,fontFamily:MONO,fontSize:11,color:C.muted}}>practice.py</div>
              <div style={{flex:1,overflow:"hidden"}}><CodeEditor value={code} onChange={setCode}/></div>
            </div>
            <div style={{width:300,display:"flex",flexDirection:"column"}}>
              <div style={{padding:"6px 16px",background:C.surfaceHigh,borderBottom:`1px solid ${C.border}`,fontFamily:MONO,fontSize:11,color:C.muted,display:"flex",justifyContent:"space-between"}}>
                <span>output</span>
                {output && <span style={{color:outputErr?C.red:C.green,fontWeight:700}}>{outputErr?"● ERROR":"● OK"}</span>}
              </div>
              <pre style={{flex:1,padding:16,fontFamily:MONO,fontSize:13,color:outputErr?C.red:C.primary,margin:0,overflowY:"auto",lineHeight:1.6,whiteSpace:"pre-wrap",background:C.surface}}>
                {output||<span style={{color:C.muted}}>Run to see output</span>}
              </pre>
            </div>
          </div>
        </div>
      ) : (
        <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:C.muted,fontFamily:SANS,flexDirection:"column",gap:12}}>
          <div style={{fontSize:40}}>📝</div>
          <div>Select a problem to start</div>
          {!backendOnline && <div style={{fontFamily:SANS,fontSize:12,color:C.red}}>⚠️ Backend offline — start the FastAPI server for full features</div>}
        </div>
      )}
    </div>
  );
}

// ─── MAIN APP ─────────────────────────────────────────────────────────────────
export default function CodeSense() {
  const [page,setPage]=useState("ide");
  const [code,setCode]=useState(
`# Welcome to CodeSense 🎓
# Python-only learning — understanding, not vibe coding.

def greet(name):
    message = f"Hello, {name}!"
    return message

names = ["Alice", "Bob", "Charlie"]

for name in names:
    if name == "Bob":
        print("Special greeting for Bob!")
    else:
        result = greet(name)
        print(result)
`);

  // Execution
  const [output,setOutput]=useState("");
  const [outputErr,setOutputErr]=useState(false);
  const [running,setRunning]=useState(false);

  // Sidebar
  const [sideTab,setSideTab]=useState("flow");
  const [hindiMode,setHindiMode]=useState(false);

  // Analysis state
  const [graph,setGraph]=useState({nodes:[],edges:[]});
  const [explanation,setExplanation]=useState("");
  const [whyText,setWhyText]=useState("");
  const [concepts,setConcepts]=useState([]);
  const [analyzeLoading,setAnalyzeLoading]=useState(false);
  const [llmLoading,setLlmLoading]=useState(false);
  const [llmBackend,setLlmBackend]=useState("Mistral-7B");

  // Skills
  const [skills,setSkills]=useState({variables:45,loops:30,conditions:20,functions:15,classes:5,recursion:0,exceptions:0,imports:5});

  // Anti-vibe
  const [antiVibe,setAntiVibe]=useState(false);
  const [showAV,setShowAV]=useState(false);
  const [avPred,setAvPred]=useState("");
  const [avResult,setAvResult]=useState(null);

  // Backend
  const [backendStatus,setBackendStatus]=useState("checking");
  const [backendOnline,setBackendOnline]=useState(false);
  const [wsReady,setWsReady]=useState(false);

  // ── Health check + WS connect ──────────────────────────────────────────────
  useEffect(()=>{
    const check=async()=>{
      try {
        const h=await api.health();
        setBackendOnline(true);
        setBackendStatus(h.llm_available?"online":"mock");
        if (h.llm_backend) setLlmBackend(h.llm_backend);
      } catch { setBackendOnline(false); setBackendStatus("offline"); }
    };
    check();
    const t=setInterval(check,30000);

    ws.connect().then(()=>{
      setWsReady(true);
      // Two-phase analyze results
      ws.on("analyze_result",(payload)=>{
        if (payload.phase==="graph"&&payload.graph) {
          const g=payload.graph;
          setGraph({nodes:g.nodes||[],edges:g.edges||[]});
          setConcepts(g.concepts||[]);
          setAnalyzeLoading(false);
          if ((g.concepts||[]).length>0) setLlmLoading(true);
          setSkills(prev=>{
            const ns={...prev};
            (g.concepts||[]).forEach(c=>{ if(ns[c]!==undefined) ns[c]=Math.min(100,(ns[c]||0)+2); });
            return ns;
          });
        }
        if (payload.phase==="explanation") {
          if (payload.plain_english) setExplanation(payload.plain_english);
          if (payload.why_this_works) setWhyText(payload.why_this_works);
          if (payload.llm_backend)   setLlmBackend(payload.llm_backend);
          setLlmLoading(false);
          if (payload.skill_updates) {
            setSkills(prev=>{
              const ns={...prev};
              Object.entries(payload.skill_updates).forEach(([k,v])=>{ ns[k]=Math.min(100,(ns[k]||0)+v); });
              return ns;
            });
          }
        }
      });
      // Execute results
      ws.on("execute_result",(payload)=>{
        if (payload.phase==="complete") {
          const isErr=payload.status!=="success";
          setOutput(isErr?(payload.stderr||`Status: ${payload.status}`):(payload.stdout||"(no output)"));
          setOutputErr(isErr);
          setRunning(false);
        }
      });
    }).catch(()=>setWsReady(false));

    return ()=>clearInterval(t);
  },[]);

  // ── Debounced live analysis ────────────────────────────────────────────────
  useEffect(()=>{
    if (!code.trim()) {
      setGraph({nodes:[],edges:[]}); setConcepts([]); setExplanation(""); setWhyText("");
      return;
    }
    const t=setTimeout(async()=>{
      setAnalyzeLoading(true);
      try {
        if (wsReady&&ws.connected) {
          ws.analyze(code,hindiMode?"hi":"en");
          // Results arrive via WS handlers above
        } else if (backendOnline) {
          const res=await api.analyze(code);
          setGraph({nodes:res.graph?.nodes||[],edges:res.graph?.edges||[]});
          setConcepts(res.concepts||[]);
          setExplanation(res.plain_english||"");
          setWhyText(res.why_this_works||"");
          if (res.llm_backend) setLlmBackend(res.llm_backend);
          if (res.skill_updates) {
            setSkills(prev=>{ const ns={...prev}; Object.entries(res.skill_updates).forEach(([k,v])=>{ ns[k]=Math.min(100,(ns[k]||0)+v); }); return ns; });
          }
          setAnalyzeLoading(false);
        } else {
          const local=localAnalyze(code);
          setGraph({nodes:local.nodes,edges:local.edges});
          setConcepts(local.concepts);
          setExplanation(local.plain_english);
          setWhyText(local.why_this_works);
          setSkills(prev=>{ const ns={...prev}; Object.entries(local.skill_updates||{}).forEach(([k,v])=>{ ns[k]=Math.min(100,(ns[k]||0)+v); }); return ns; });
          setAnalyzeLoading(false);
        }
      } catch {
        const local=localAnalyze(code);
        setGraph({nodes:local.nodes,edges:local.edges});
        setConcepts(local.concepts);
        setExplanation(local.plain_english);
        setWhyText(local.why_this_works);
        setAnalyzeLoading(false);
      }
    },600);
    return ()=>clearTimeout(t);
  },[code,hindiMode,backendOnline,wsReady]);

  // ── Execute code ───────────────────────────────────────────────────────────
  const executeCode=useCallback(async()=>{
    if (!code.trim()||running) return;
    setRunning(true); setOutput(""); setOutputErr(false);
    try {
      if (wsReady&&ws.connected) {
        ws.execute(code);
        // Result arrives via WS handler; running cleared there
      } else if (backendOnline) {
        const res=await api.execute(code);
        const isErr=res.status!=="success";
        setOutput(isErr?(res.stderr||`Status: ${res.status}`):(res.stdout||"(no output)"));
        setOutputErr(isErr);
        setRunning(false);
      } else {
        setOutput("⚠️ Backend offline — start the FastAPI server to execute code.");
        setOutputErr(true); setRunning(false);
      }
    } catch(e) { setOutput("Error: "+e.message); setOutputErr(true); setRunning(false); }
  },[code,running,backendOnline,wsReady]);

  const runCode=useCallback(()=>{
    if (antiVibe&&avResult===null&&!showAV) { setShowAV(true); return; }
    executeCode();
  },[antiVibe,avResult,showAV,executeCode]);

  // Anti-vibe: run code to compare with prediction
  const handlePredict=useCallback(async()=>{
    setRunning(true);
    let actual="";
    try {
      if (backendOnline) {
        const res=await api.execute(code);
        actual=res.status==="success"?(res.stdout||"").trim():(res.stderr||"").trim();
        setOutput(res.status==="success"?res.stdout||"":res.stderr||"");
        setOutputErr(res.status!=="success");
      }
    } catch {}
    const correct=avPred.trim()===actual;
    setAvResult({correct,actual,prediction:avPred.trim()});
    setRunning(false);
  },[avPred,code,backendOnline]);

  const resetAV=()=>{ setAvResult(null); setAvPred(""); setShowAV(false); };

  // ─── Style helpers ─────────────────────────────────────────────────────────
  const navBtn=p=>({padding:"8px 16px",cursor:"pointer",fontFamily:SANS,fontSize:13,fontWeight:600,color:page===p?C.primary:C.secondary,borderBottom:page===p?`2px solid ${C.accent}`:"2px solid transparent",background:"transparent",border:"none",transition:"color 0.15s"});
  const tabBtn=t=>({padding:"5px 11px",cursor:"pointer",fontFamily:SANS,fontSize:11,fontWeight:600,color:sideTab===t?C.accent:C.muted,background:sideTab===t?C.accentSoft:"transparent",border:`1px solid ${sideTab===t?C.accent+"50":"transparent"}`,borderRadius:6,transition:"all 0.15s"});

  return (
    <div style={{height:"100vh",display:"flex",flexDirection:"column",background:C.bg,color:C.primary,fontFamily:SANS,overflow:"hidden"}}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=DM+Sans:wght@400;500;600;700&display=swap');
        *{box-sizing:border-box}
        ::-webkit-scrollbar{width:6px;height:6px}
        ::-webkit-scrollbar-track{background:${C.surfaceHigh}}
        ::-webkit-scrollbar-thumb{background:${C.borderBright};border-radius:3px}
        ::-webkit-scrollbar-thumb:hover{background:${C.muted}}
        @keyframes pulse{0%,100%{opacity:0.3;transform:scale(0.85)}50%{opacity:1;transform:scale(1)}}
        @keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
        @keyframes spin{to{transform:rotate(360deg)}}
        input::placeholder{color:${C.muted}}
        textarea::placeholder{color:${C.muted}}
        button:hover{filter:brightness(0.96)}
      `}</style>

      {/* HEADER */}
      <header style={{borderBottom:`1px solid ${C.border}`,background:C.surface,boxShadow:"0 1px 4px rgba(0,0,0,0.06)",display:"flex",alignItems:"center",padding:"0 20px",height:52,flexShrink:0,gap:20}}>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          <div style={{width:28,height:28,borderRadius:8,background:C.accent,display:"flex",alignItems:"center",justifyContent:"center",fontSize:15}}>🧠</div>
          <span style={{fontWeight:800,fontSize:17,letterSpacing:-0.5,color:C.primary}}>Code<span style={{color:C.accent}}>Sense</span></span>
        </div>
        <nav style={{display:"flex",flex:1,borderLeft:`1px solid ${C.border}`,paddingLeft:16}}>
          <button style={navBtn("ide")}      onClick={()=>setPage("ide")}>⚡ IDE</button>
          <button style={navBtn("practice")} onClick={()=>setPage("practice")}>📚 Practice</button>
          <button style={navBtn("mentor")}   onClick={()=>setPage("mentor")}>🧑‍🏫 Mentor</button>
        </nav>
        <div style={{display:"flex",gap:10,alignItems:"center"}}>
          <BackendBadge status={backendStatus}/>
          <label style={{display:"flex",alignItems:"center",gap:6,cursor:"pointer",fontFamily:SANS,fontSize:12,color:hindiMode?C.accent:C.secondary}}>
            <div onClick={()=>setHindiMode(h=>!h)} style={{width:32,height:17,borderRadius:9,background:hindiMode?C.accent:C.border,position:"relative",transition:"background 0.2s",cursor:"pointer"}}>
              <div style={{position:"absolute",top:2,left:hindiMode?15:2,width:13,height:13,borderRadius:"50%",background:"#fff",transition:"left 0.2s"}}/>
            </div>
            हिंदी
          </label>
          <label style={{display:"flex",alignItems:"center",gap:6,cursor:"pointer",fontFamily:SANS,fontSize:12,color:antiVibe?C.yellow:C.secondary}}>
            <div onClick={()=>{setAntiVibe(a=>!a);resetAV();}} style={{width:32,height:17,borderRadius:9,background:antiVibe?C.yellow:C.border,position:"relative",transition:"background 0.2s",cursor:"pointer"}}>
              <div style={{position:"absolute",top:2,left:antiVibe?15:2,width:13,height:13,borderRadius:"50%",background:"#fff",transition:"left 0.2s"}}/>
            </div>
            🛑 Anti-Vibe
          </label>
        </div>
      </header>

      <div style={{flex:1,overflow:"hidden"}}>

        {/* ── IDE PAGE ─────────────────────────────────────────────────────── */}
        {page==="ide" && (
          <div style={{display:"flex",height:"100%"}}>

            {/* LEFT — analysis sidebar */}
            <div style={{width:300,borderRight:`1px solid ${C.border}`,display:"flex",flexDirection:"column",background:C.surface,flexShrink:0}}>
              <div style={{padding:"10px 12px",borderBottom:`1px solid ${C.border}`,display:"flex",gap:5,flexWrap:"wrap"}}>
                {[["flow","🔀 Flow"],["explain","💬 Explain"],["why","🤔 Why"],["skills","📊 Skills"]].map(([t,l])=>(
                  <button key={t} onClick={()=>setSideTab(t)} style={tabBtn(t)}>{l}</button>
                ))}
              </div>
              <div style={{flex:1,overflowY:"auto",padding:14}}>
                {sideTab==="flow" && <Flowchart nodes={graph.nodes} edges={graph.edges} loading={analyzeLoading}/>}

                {sideTab==="explain" && (
                  <div style={{animation:"fadeIn 0.3s"}}>
                    {(analyzeLoading||llmLoading) ? (
                      <div style={{display:"flex",alignItems:"center",gap:8,color:C.muted,fontFamily:SANS,fontSize:12}}>
                        <div style={{width:14,height:14,border:`2px solid ${C.accent}`,borderTopColor:"transparent",borderRadius:"50%",animation:"spin 0.8s linear infinite"}}/>
                        {analyzeLoading?"Building graph…":"Mistral-7B explaining…"}
                      </div>
                    ) : (
                      <>
                        <div style={{fontFamily:SANS,fontSize:13,color:C.secondary,lineHeight:1.7,marginBottom:14}}>
                          {explanation||"Start typing to see a plain-English explanation."}
                        </div>
                        {concepts.length>0 && (
                          <>
                            <div style={{fontFamily:SANS,fontSize:10,color:C.muted,fontWeight:700,marginBottom:8,letterSpacing:1,textTransform:"uppercase"}}>Concepts Detected</div>
                            <div style={{display:"flex",flexWrap:"wrap",gap:6}}>
                              {concepts.map(c=>(
                                <span key={c} style={{padding:"3px 9px",borderRadius:20,background:C.accentSoft,border:`1px solid ${C.accent}40`,fontFamily:MONO,fontSize:11,color:C.accent}}>{c}</span>
                              ))}
                            </div>
                          </>
                        )}
                      </>
                    )}
                  </div>
                )}

                {sideTab==="why" && (
                  <div style={{animation:"fadeIn 0.3s"}}>
                    {llmLoading ? (
                      <div style={{display:"flex",alignItems:"center",gap:8,color:C.muted,fontFamily:SANS,fontSize:12}}>
                        <div style={{width:14,height:14,border:`2px solid ${C.purple}`,borderTopColor:"transparent",borderRadius:"50%",animation:"spin 0.8s linear infinite"}}/>
                        Generating reasoning…
                      </div>
                    ) : (
                      <div style={{fontFamily:SANS,fontSize:13,color:C.secondary,lineHeight:1.7}}>
                        {whyText||"Python executes your code top-to-bottom, one statement at a time."}
                      </div>
                    )}
                    <div style={{marginTop:16,padding:12,background:C.surfaceHigh,borderRadius:8,border:`1px solid ${C.border}`}}>
                      <div style={{fontFamily:SANS,fontSize:10,color:C.muted,fontWeight:700,marginBottom:6,textTransform:"uppercase",letterSpacing:1}}>Powered by</div>
                      <div style={{fontFamily:MONO,fontSize:11,color:C.purple}}>⚡ {llmBackend}</div>
                      <div style={{fontFamily:SANS,fontSize:11,color:C.muted,marginTop:3}}>
                        {wsReady&&ws.connected?"🔌 WebSocket live":backendOnline?"🔗 REST API":"⚠️ Local fallback"}
                      </div>
                    </div>
                  </div>
                )}

                {sideTab==="skills" && <SkillMeter skills={skills}/>}
              </div>
            </div>

            {/* CENTER — editor + output */}
            <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
              <div style={{background:C.surfaceHigh,borderBottom:`1px solid ${C.border}`,padding:"6px 16px",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                <span style={{fontFamily:MONO,fontSize:11,color:C.muted}}>main.py · Python 3.11</span>
                <button onClick={runCode} disabled={running}
                  style={{background:running?C.border:C.accent,border:"none",borderRadius:8,padding:"5px 18px",color:running?C.muted:"#fff",cursor:running?"not-allowed":"pointer",fontFamily:SANS,fontSize:12,fontWeight:700,display:"flex",alignItems:"center",gap:6,transition:"all 0.15s"}}>
                  {running?(
                    <><div style={{width:12,height:12,border:"2px solid #fff8",borderTopColor:"#fff",borderRadius:"50%",animation:"spin 0.7s linear infinite"}}/>Running…</>
                  ):(
                    <>▶ Run Code {antiVibe&&avResult===null&&<span style={{fontSize:10,color:C.yellow}}>🛑</span>}</>
                  )}
                </button>
              </div>

              <div style={{flex:1,position:"relative",overflow:"hidden"}}>
                {/* Line numbers */}
                <div style={{position:"absolute",left:0,top:0,bottom:0,width:42,background:C.surfaceHigh,borderRight:`1px solid ${C.border}`,paddingTop:16,overflowY:"hidden",userSelect:"none"}}>
                  {code.split("\n").map((_,i)=>(
                    <div key={i} style={{fontFamily:MONO,fontSize:12,color:C.muted,textAlign:"right",paddingRight:10,lineHeight:"23.8px"}}>{i+1}</div>
                  ))}
                </div>
                <div style={{position:"absolute",left:42,right:0,top:0,bottom:0}}>
                  <CodeEditor value={code} onChange={setCode}/>
                </div>

                {antiVibe&&showAV && (
                  <AntiVibeOverlay
                    onPredict={handlePredict}
                    prediction={avPred} setPrediction={setAvPred}
                    checking={running} result={avResult}
                    onDismiss={resetAV}
                  />
                )}
              </div>

              {/* Output panel */}
              <div style={{height:160,borderTop:`1px solid ${C.border}`,display:"flex",flexDirection:"column",flexShrink:0}}>
                <div style={{padding:"5px 16px",background:C.surfaceHigh,borderBottom:`1px solid ${C.border}`,display:"flex",gap:12,alignItems:"center"}}>
                  <span style={{fontFamily:MONO,fontSize:11,color:C.muted}}>output</span>
                  {running && <span style={{fontFamily:SANS,fontSize:10,color:C.yellow,fontWeight:700}}>● RUNNING</span>}
                  {!running&&output && <span style={{fontFamily:SANS,fontSize:10,color:outputErr?C.red:C.green,fontWeight:700}}>{outputErr?"● ERROR":"● COMPLETE"}</span>}
                </div>
                <pre style={{flex:1,padding:"10px 16px",fontFamily:MONO,fontSize:13,color:outputErr?C.red:C.primary,margin:0,overflowY:"auto",lineHeight:1.6,whiteSpace:"pre-wrap",background:C.surface}}>
                  {output||<span style={{color:C.muted}}>Press Run Code to execute…</span>}
                </pre>
              </div>
            </div>

            {/* RIGHT — mentor */}
            <div style={{width:280,borderLeft:`1px solid ${C.border}`,display:"flex",flexDirection:"column",background:C.surface,flexShrink:0}}>
              <div style={{padding:"10px 14px",borderBottom:`1px solid ${C.border}`}}>
                <div style={{fontFamily:SANS,fontSize:13,fontWeight:700,color:C.primary}}>🧑‍🏫 Mentor</div>
                <div style={{fontFamily:SANS,fontSize:11,color:C.muted}}>Hints only — never answers · {backendOnline?<span style={{color:C.green}}>Mistral-7B live</span>:<span style={{color:C.yellow}}>offline mode</span>}</div>
              </div>
              <div style={{flex:1,overflow:"hidden"}}>
                <MentorChat currentCode={code} currentError={outputErr?output:""} backendOnline={backendOnline} hindiMode={hindiMode}/>
              </div>
            </div>
          </div>
        )}

        {/* ── PRACTICE PAGE ──────────────────────────────────────────────── */}
        {page==="practice" && (
          <div style={{height:"100%",background:C.bg}}>
            <PracticePage skills={skills} setSkills={setSkills} backendOnline={backendOnline} hindiMode={hindiMode}/>
          </div>
        )}

        {/* ── MENTOR PAGE ────────────────────────────────────────────────── */}
        {page==="mentor" && (
          <div style={{height:"100%",display:"flex",flexDirection:"column"}}>
            <div style={{padding:"16px 24px",borderBottom:`1px solid ${C.border}`,background:C.surface}}>
              <h2 style={{margin:0,fontFamily:SANS,fontSize:18,fontWeight:800}}>🧑‍🏫 Mentor Session</h2>
              <p style={{margin:"4px 0 0",fontFamily:SANS,fontSize:13,color:C.secondary,display:"flex",alignItems:"center",gap:8}}>
                Socratic method — questions that make you think, not answers that make you copy.
                <span style={{color:backendOnline?C.green:C.yellow,fontWeight:600}}>
                  {backendOnline?`● ${llmBackend} live`:"● Offline mode"}
                </span>
              </p>
            </div>
            <div style={{flex:1,overflow:"hidden",maxWidth:720,margin:"0 auto",width:"100%",paddingTop:12,display:"flex",flexDirection:"column"}}>
              <MentorChat currentCode={code} currentError={outputErr?output:""} backendOnline={backendOnline} hindiMode={hindiMode}/>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
