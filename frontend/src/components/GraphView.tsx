/**
 * Interactive identity graph (Cytoscape.js).
 * Zoom, pan, filtering by node type and by minimum score.
 */
import { useQuery } from '@tanstack/react-query'
import cytoscape from 'cytoscape'
import type { Core } from 'cytoscape'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Empty, ErrorBox, Loading } from '@/components/ui'
import { api, query } from '@/lib/api'
import type { GraphData } from '@/types'

const NODE_COLORS: Record<string, string> = {
  person: '#38bdf8',
  identifier: '#22c55e',
  username: '#a855f7',
  social_profile: '#f59e0b',
  platform: '#64748b',
  organization: '#e879f9',
}

const NODE_LABELS: Record<string, string> = {
  person: 'Person',
  identifier: 'Identifier',
  username: 'Username',
  social_profile: 'Social profile',
  platform: 'Platform',
  organization: 'Organisation',
}

const ALL_TYPES = Object.keys(NODE_COLORS)

export default function GraphView({
  endpoint,
  height = 520,
}: {
  endpoint: string
  height?: number
}) {
  const container = useRef<HTMLDivElement>(null)
  const instance = useRef<Core | null>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [minConfidence, setMinConfidence] = useState(0)
  const [selected, setSelected] = useState<GraphData['nodes'][number] | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['graph', endpoint, minConfidence],
    queryFn: () => api.get<GraphData>(`${endpoint}${query({ min_confidence: minConfidence })}`),
  })

  const elements = useMemo(() => {
    if (!data) return []
    const visibleNodes = data.nodes.filter((node) => !hidden.has(node.type))
    const ids = new Set(visibleNodes.map((node) => node.id))
    return [
      ...visibleNodes.map((node) => ({
        data: {
          id: node.id,
          label: node.label.length > 26 ? `${node.label.slice(0, 24)}...` : node.label,
          type: node.type,
          variant: node.is_variant ? 'yes' : 'no',
        },
      })),
      ...data.edges
        .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
        .map((edge) => ({
          data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: edge.type,
            confidence: edge.confidence,
          },
        })),
    ]
  }, [data, hidden])

  useEffect(() => {
    if (!container.current) return
    const core = cytoscape({
      container: container.current,
      elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': (node) => NODE_COLORS[node.data('type')] ?? '#64748b',
            label: 'data(label)',
            color: '#cbd5e1',
            'font-size': '9px',
            'text-valign': 'bottom',
            'text-margin-y': 4,
            width: 22,
            height: 22,
            'border-width': 1,
            'border-color': '#0b0f14',
          },
        },
        {
          selector: 'node[type = "person"]',
          style: { width: 38, height: 38, 'font-size': '11px', 'font-weight': 'bold' },
        },
        {
          // Hypotheses are visually distinct from established facts.
          selector: 'node[variant = "yes"]',
          style: { 'border-width': 2, 'border-style': 'dashed', 'border-color': '#a855f7' },
        },
        {
          selector: 'edge',
          style: {
            width: 1,
            'line-color': '#26303d',
            'target-arrow-color': '#26303d',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            label: 'data(label)',
            'font-size': '7px',
            color: '#475569',
            'text-rotation': 'autorotate',
          },
        },
        {
          selector: ':selected',
          style: { 'border-width': 3, 'border-color': '#38bdf8', 'line-color': '#38bdf8' },
        },
      ],
      layout: { name: 'cose', animate: false, nodeRepulsion: 9000, idealEdgeLength: 90 },
      wheelSensitivity: 0.25,
    })

    core.on('tap', 'node', (event) => {
      const id = event.target.id()
      setSelected(data?.nodes.find((node) => node.id === id) ?? null)
    })
    core.on('tap', (event) => {
      if (event.target === core) setSelected(null)
    })

    instance.current = core
    return () => {
      core.destroy()
      instance.current = null
    }
  }, [elements, data])

  if (isLoading) return <Loading label="Building the graph..." />
  if (error) return <ErrorBox error={error} />
  if (!data || data.nodes.length === 0) {
    return <Empty message="Empty graph: add information or launch a search." />
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        {ALL_TYPES.map((type) => (
          <button
            key={type}
            className={`text-xs px-2 py-1 rounded border transition-opacity ${
              hidden.has(type) ? 'opacity-35' : ''
            }`}
            style={{ borderColor: NODE_COLORS[type], color: NODE_COLORS[type] }}
            onClick={() => {
              const next = new Set(hidden)
              next.has(type) ? next.delete(type) : next.add(type)
              setHidden(next)
            }}
          >
            ● {NODE_LABELS[type]} ({data.stats.by_type[type] ?? 0})
          </button>
        ))}

        <label className="flex items-center gap-2 text-xs text-slate-400 ml-auto">
          Minimum score: {Math.round(minConfidence * 100)}%
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={minConfidence * 100}
            onChange={(event) => setMinConfidence(Number(event.target.value) / 100)}
          />
        </label>

        <button className="btn" onClick={() => instance.current?.fit(undefined, 40)}>
          Recentrer
        </button>
      </div>

      <div
        ref={container}
        style={{ height }}
        className="w-full rounded-md border border-line bg-base-900"
      />

      {selected && (
        <div className="card p-3 text-sm">
          <p className="text-slate-200 font-medium">{selected.label}</p>
          <p className="text-xs text-slate-500">
            {NODE_LABELS[selected.type] ?? selected.type}
            {selected.subtype ? ` · ${selected.subtype}` : ''}
            {selected.status ? ` · ${selected.status}` : ''}
            {selected.confidence !== undefined
              ? ` · ${Math.round(selected.confidence * 100)}%`
              : ''}
          </p>
          {selected.is_variant && (
            <p className="text-xs text-hypo mt-1">
              Hypothetical variant: does not necessarily belong to this person.
            </p>
          )}
          {selected.url && (
            <a
              className="text-accent text-xs hover:underline"
              href={selected.url}
              target="_blank"
              rel="noreferrer noopener"
            >
              {selected.url}
            </a>
          )}
        </div>
      )}

      <p className="text-xs text-slate-600">
        {data.stats.nodes} nodes and {data.stats.edges} relationships. Purple dashed
        nodes are generated hypotheses, never established facts.
      </p>
    </div>
  )
}
