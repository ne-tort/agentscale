import type { ModuleSlotRegistry, ModuleSlotRenderer } from "./contracts";

export type ModuleSlotKey = `${string}:${string}:${string}`;

export class MapModuleSlotRegistry implements ModuleSlotRegistry {
  private readonly renderers = new Map<ModuleSlotKey, ModuleSlotRenderer>();

  register(moduleId: string, viewSlug: string, slot: string, renderer: ModuleSlotRenderer): () => void {
    const key = `${moduleId}:${viewSlug}:${slot}` as ModuleSlotKey;
    this.renderers.set(key, renderer);
    return () => {
      if (this.renderers.get(key) === renderer) this.renderers.delete(key);
    };
  }

  resolve(moduleId: string, viewSlug: string, slot: string): ModuleSlotRenderer | undefined {
    return this.renderers.get(`${moduleId}:${viewSlug}:${slot}` as ModuleSlotKey);
  }
}

export function createModuleSlotRegistry(): MapModuleSlotRegistry {
  return new MapModuleSlotRegistry();
}
