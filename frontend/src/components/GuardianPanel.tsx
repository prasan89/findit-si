'use client'
import type { Recording } from '@/types'
import GuardianDashboard from './GuardianDashboard'

interface Props {
  recordings: Recording[]
}

export default function GuardianPanel({ recordings }: Props) {
  return <GuardianDashboard recordings={recordings} />
}
