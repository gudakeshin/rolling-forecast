/** Data behind the composer's `/` and `@` menus (plan 6.2): registered skills
 * the user may run, and line items / drivers / versions to mention. */
import { apiGet } from '../../api/client';
import { searchLineItems } from '../../api/dashboard';
import { listDrivers, type Driver } from '../../api/drivers';
import type { User } from '../../types/auth';
import { useVersionStore } from '../../store/versionStore';

export interface SkillCommand {
  /** Skill name, typed after `/`. */
  id: string;
  description: string;
  /** Text placed in the composer for the user to finish (skills need details). */
  prompt: string;
}

export type MentionKind = 'line_item' | 'driver' | 'version';

export interface MentionOption {
  key: string;
  kind: MentionKind;
  name: string;
  detail: string;
  /** What goes into the message: kind-qualified so the agent can resolve it. */
  insert: string;
}

interface SkillInfo {
  name: string;
  description: string;
  required_role: string | null;
}

const PERMISSION_FLAG: Record<string, keyof User> = {
  input: 'can_input',
  generate: 'can_generate',
  override: 'can_override',
  review: 'can_review',
  publish: 'can_publish',
  admin: 'can_admin',
};

/** Skills a user may run: unrestricted ones plus those whose role flag they hold. */
export function skillCommandsFor(skills: SkillInfo[], user: User | null): SkillCommand[] {
  return skills
    .filter((s) => {
      const flag = s.required_role ? PERMISSION_FLAG[s.required_role] : undefined;
      return !flag || Boolean(user?.[flag]);
    })
    .map((s) => ({
      id: s.name,
      description: s.description,
      prompt: `Use ${s.name}: `,
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

let skillsCache: Promise<SkillInfo[]> | null = null;

export function loadSkills(): Promise<SkillInfo[]> {
  if (!skillsCache) {
    skillsCache = apiGet<{ skills: SkillInfo[] }>('/skills/')
      .then((r) => r.skills ?? [])
      .catch(() => {
        skillsCache = null; // retry next time
        return [];
      });
  }
  return skillsCache;
}

const DRIVER_TTL_MS = 60_000;
let driverCache: { at: number; drivers: Promise<Driver[]> } | null = null;

function cachedDrivers(now = Date.now()): Promise<Driver[]> {
  if (!driverCache || now - driverCache.at > DRIVER_TTL_MS) {
    driverCache = { at: now, drivers: listDrivers().catch(() => []) };
  }
  return driverCache.drivers;
}

export function resetComposerSourceCaches(): void {
  skillsCache = null;
  driverCache = null;
}

const PER_KIND = 4;

/** Line items (server search), drivers and versions matching `query`. */
export async function searchMentions(query: string): Promise<MentionOption[]> {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const [items, drivers] = await Promise.all([
    searchLineItems(query, PER_KIND).then((r) => r.items ?? []).catch(() => []),
    cachedDrivers(),
  ]);
  const versions = useVersionStore.getState().versions;
  return [
    ...items.slice(0, PER_KIND).map<MentionOption>((li) => ({
      key: `line_item:${li.id}`,
      kind: 'line_item',
      name: li.name,
      detail: li.account_code,
      insert: li.name,
    })),
    ...drivers
      .filter((d) => d.name.toLowerCase().includes(q) || d.key.toLowerCase().includes(q))
      .slice(0, PER_KIND)
      .map<MentionOption>((d) => ({
        key: `driver:${d.id}`,
        kind: 'driver',
        name: d.name,
        detail: d.key,
        insert: `driver "${d.name}"`,
      })),
    ...versions
      .filter((v) => v.name.toLowerCase().includes(q))
      .slice(0, PER_KIND)
      .map<MentionOption>((v) => ({
        key: `version:${v.id}`,
        kind: 'version',
        name: v.name,
        detail: v.status,
        insert: `version ${v.name}`,
      })),
  ];
}
