export type ServiceMode = 'local' | 'lan' | 'all'

export interface NavigationGroup {
  id: string
  name: string
  photoCount: number
  cropCount: number
  preferredCount: number
}

export interface Source {
  id: string
  path: string
  name: string
  status: string
  error?: string
  job?: string
  progress?: {
    phase?: string
    stage?: 'scan' | 'detection' | 'metadata' | 'birder' | 'gallery'
    totalPhotos?: number
    processedPhotos?: number
    totalGroups?: number
    completedGroups?: number
    cropCount?: number
    processed?: number
    total?: number
    message?: string
  }
}

export interface State {
  version: 1
  instanceId: string
  runtime: {
    ready: boolean
    mode?: 'packaged-verified' | 'development-unverified'
    models: Array<{ id: string; path: string; ready: boolean; verified?: boolean; error?: string }>
  }
  sources: Source[]
  outputRoot: string
  service: {
    status: string
    port: number
    mode: ServiceMode
    localUrl: string | null
    lanUrls: string[]
    error?: string | null
    suggestedPort?: number | null
  }
}

export interface DesktopAPI {
  getState(): Promise<State>
  chooseSource(): Promise<State>
  attachExisting(): Promise<State>
  chooseOutput(): Promise<State>
  removeSource(id: string): Promise<State>
  rescan(id: string): Promise<State>
  configureService(config: { mode: ServiceMode; port: number; lanIp?: string }): Promise<State>
  onState(listener: (state: State) => void): () => void
}

declare global {
  interface Window {
    birdshotDesktop?: DesktopAPI
  }
}
