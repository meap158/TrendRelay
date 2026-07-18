export interface PluginManifest {
  id: string;
  name: string;
  version: string;
  capabilities: string[];
  allowedNetworkDomains: string[];
  requiredSecrets: string[];
}

export interface PluginContext { workspaceId: string; operationId: string; signal: AbortSignal; }
