import { Avatar, Chip, Separator, Surface, Typography } from "@heroui/react";
import {
  Droplet,
  Footprints,
  House,
  Moon,
  Settings,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

export type DashboardView = "overview" | "steps" | "sleep" | "water-intake" | "settings";

type DashboardShellProps = {
  activeView: DashboardView;
  children: ReactNode;
  email: string;
  syncLabel: string;
};

const navigation: Array<{
  id: DashboardView;
  href: string;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "overview", href: "/dashboard", label: "Dashboard", icon: House },
  { id: "steps", href: "/steps", label: "Steps", icon: Footprints },
  { id: "sleep", href: "/sleep", label: "Sleep", icon: Moon },
  { id: "water-intake", href: "/water-intake", label: "Water intake", icon: Droplet },
];

function navigationClass(active: boolean): string {
  return [
    "flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors",
    active
      ? "bg-accent-soft text-accent-soft-foreground"
      : "text-muted hover:bg-surface-tertiary hover:text-foreground",
  ].join(" ");
}

export function DashboardShell({
  activeView,
  children,
  email,
  syncLabel,
}: DashboardShellProps) {
  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-20 w-64 border-r border-border max-lg:hidden">
        <Surface className="flex h-full flex-col rounded-none" variant="secondary">
          <div className="flex items-center gap-3 p-5">
            <Avatar color="accent" size="sm">
              <Avatar.Fallback>LS</Avatar.Fallback>
            </Avatar>
            <div className="min-w-0">
              <Typography weight="bold">LifeStats</Typography>
            </div>
          </div>

          <Separator />

          <nav className="grid flex-1 content-start gap-1 px-3 py-2" aria-label="Primary">
            {navigation.map((item) => {
              const Icon = item.icon;
              return (
                <Link
                  aria-current={activeView === item.id ? "page" : undefined}
                  className={navigationClass(activeView === item.id)}
                  href={item.href}
                  key={item.id}
                >
                  <Icon className="size-5 shrink-0" aria-hidden="true" />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="px-3 pb-3">
            <Link
              aria-current={activeView === "settings" ? "page" : undefined}
              className={navigationClass(activeView === "settings")}
              href="/settings"
            >
              <Settings className="size-5 shrink-0" aria-hidden="true" />
              Settings
            </Link>
          </div>

          <Separator />

          <footer className="flex items-center gap-3 p-4">
            <Avatar size="sm">
              <Avatar.Fallback>{email.charAt(0).toUpperCase()}</Avatar.Fallback>
            </Avatar>
            <div className="min-w-0">
              <Typography className="block" truncate type="body-xs" weight="semibold">
                {email}
              </Typography>
              <Typography className="block" color="muted" type="body-xs">
                Private account
              </Typography>
            </div>
          </footer>
        </Surface>
      </aside>

      <Surface
        className="fixed inset-x-0 top-0 z-20 flex h-16 items-center gap-3 rounded-none border-b border-border px-4 lg:hidden"
        variant="secondary"
      >
        <Avatar color="accent" size="sm">
          <Avatar.Fallback>LS</Avatar.Fallback>
        </Avatar>
        <Typography className="flex-1" weight="bold">
          LifeStats
        </Typography>
        <Chip color="success" size="sm" variant="soft">
          <Chip.Label>{syncLabel}</Chip.Label>
        </Chip>
        <Link
          className="grid size-10 place-items-center rounded-xl text-lg text-muted hover:bg-surface-tertiary"
          aria-label="Settings"
          href="/settings"
        >
          <Settings className="size-5" aria-hidden="true" />
        </Link>
      </Surface>

      <main className="min-h-screen px-7 py-9 pb-24 lg:ml-64 max-lg:pt-24 max-sm:px-4">
        {children}
      </main>

      <Surface
        className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-4 rounded-none border-t border-border pb-[env(safe-area-inset-bottom)] lg:hidden"
        variant="secondary"
      >
        {navigation.map((item) => {
          const Icon = item.icon;
          return (
            <Link
              aria-current={activeView === item.id ? "page" : undefined}
              className={`grid min-h-16 place-items-center content-center gap-1 text-xs ${
                activeView === item.id ? "text-accent" : "text-muted"
              }`}
              href={item.href}
              key={item.id}
            >
              <Icon className="size-5" aria-hidden="true" />
              {item.label}
            </Link>
          );
        })}
      </Surface>
    </div>
  );
}
