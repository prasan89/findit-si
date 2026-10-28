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
  const color = usage.is_near_limit ? 'text-orange-600' : 'text-gray-500'

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`text-xs ${color} hover:text-gray-700 flex items-center gap-1`}
        title="AI credit usage"
      >
        <span className={`inline-block w-12 h-1.5 rounded-full bg-gray-200 overflow-hidden`}>
          <span
            className={`block h-full rounded-full ${usage.is_near_limit ? 'bg-orange-500' : 'bg-indigo-400'}`}
            style={{ width: `${pct}%` }}
          />
        </span>
        <span>${usage.total_spent_usd.toFixed(4)} / ${usage.budget_limit_usd.toFixed(0)}</span>
      </button>
      {open && (
        <div className="absolute right-0 top-6 z-10 bg-white rounded-xl border border-gray-200 shadow-lg p-4 min-w-[240px] text-xs">
          <div className="font-semibold text-gray-800 mb-2">AI Credit Usage</div>
          <div className="flex justify-between text-gray-600 mb-1">
            <span>Spent</span><span>${usage.total_spent_usd.toFixed(4)}</span>
          </div>
          <div className="flex justify-between text-gray-600 mb-2">
            <span>Remaining</span>
            <span className={usage.is_near_limit ? 'text-orange-600 font-medium' : ''}>
              ${usage.remaining_usd.toFixed(4)}
            </span>
          </div>
          {usage.by_model.map((m, i) => (
            <div key={i} className="flex justify-between text-gray-500 border-t pt-1 mt-1">
              <span>{m.model} ({m.calls} calls)</span>
              <span>${m.cost_usd.toFixed(4)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
