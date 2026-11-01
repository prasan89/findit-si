'use client'
import { useState, useEffect } from 'react'

interface UsageData {
  total_spent_usd: number
  budget_limit_usd: number
  remaining_usd: number
  is_near_limit: boolean
  by_model: { provider: string; model: string; calls: number; cost_usd: number }[]
}

export default function UsageBadge() {
  const [usage, setUsage] = useState<UsageData | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const load = () =>
      fetch('/api/usage/')
        .then(r => r.ok ? r.json() : null)
        .then(d => d && setUsage(d))
        .catch(() => {})
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [])

  if (!usage) return null

  const pct = Math.min((usage.total_spent_usd / usage.budget_limit_usd) * 100, 100)
  const isNear = usage.is_near_limit

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-xs text-[#64748B] hover:text-[#0F172A] transition-colors"
        title="AI credit usage"
      >
        <div className="w-16 h-1.5 rounded-full bg-[#E2E8F0] overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${isNear ? 'bg-amber-500' : 'bg-[#2563EB]'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="font-medium tabular-nums">
          ${usage.total_spent_usd.toFixed(4)}<span className="text-[#94A3B8]"> / ${usage.budget_limit_usd.toFixed(0)}</span>
        </span>
      </button>

      {open && (
        <div className="absolute right-0 top-7 z-50 bg-white rounded-xl border border-[#E2E8F0] shadow-lg p-4 min-w-[240px]">
          <div className="text-xs font-semibold text-[#0F172A] mb-3">AI Credit Usage</div>
          <div className="space-y-1.5 text-xs">
            <div className="flex justify-between text-[#64748B]">
              <span>Spent</span>
              <span className="font-medium text-[#0F172A] tabular-nums">${usage.total_spent_usd.toFixed(4)}</span>
            </div>
            <div className="flex justify-between text-[#64748B]">
              <span>Remaining</span>
              <span className={`font-medium tabular-nums ${isNear ? 'text-amber-600' : 'text-[#16A34A]'}`}>
                ${usage.remaining_usd.toFixed(4)}
              </span>
            </div>
            <div className="pt-2 mt-2 border-t border-[#E2E8F0] space-y-1">
              {usage.by_model.map((m, i) => (
                <div key={i} className="flex justify-between text-[#94A3B8]">
                  <span className="truncate max-w-[160px]">{m.model} ({m.calls} calls)</span>
                  <span className="tabular-nums ml-2">${m.cost_usd.toFixed(4)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
