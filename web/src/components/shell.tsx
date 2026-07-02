import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  Boxes,
  ClipboardList,
  Menu,
  Moon,
  Server,
  ShieldCheck,
  Sun,
  UserCog,
  Users,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "./ui";
import { cn } from "../lib/utils";

const navItems = [
  { to: "/", label: "Overview", icon: Activity },
  { to: "/runs", label: "Runs", icon: ClipboardList },
  { to: "/executors", label: "Executors", icon: Server },
  { to: "/missions", label: "Missions", icon: Boxes },
  { to: "/profiles", label: "Profiles", icon: UserCog },
  { to: "/access", label: "Access", icon: Users },
  { to: "/operations", label: "Operations", icon: ShieldCheck },
] as const;

export function Shell() {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-30 border-b border-border bg-card/95 backdrop-blur">
        <div className="flex h-14 items-center justify-between gap-3 px-4 lg:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              aria-label="Open navigation"
              className="lg:hidden"
              size="icon"
              variant="ghost"
              onClick={() => setOpen(true)}
            >
              <Menu className="h-4 w-4" />
            </Button>
            <div className="grid min-w-0">
              <div className="truncate text-sm font-semibold">
                Cloud Agents Runtime
              </div>
              <div className="truncate text-xs text-muted-foreground">
                SAEU Control Plane
              </div>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <div className="grid lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="sticky top-14 hidden h-[calc(100vh-3.5rem)] border-r border-border bg-card lg:block">
          <Navigation />
        </aside>
        {open ? (
          <div className="fixed inset-0 z-40 bg-background/80 backdrop-blur lg:hidden">
            <div className="h-full w-72 border-r border-border bg-card">
              <div className="flex h-14 items-center justify-between border-b border-border px-4">
                <span className="text-sm font-semibold">Navigation</span>
                <Button
                  aria-label="Close navigation"
                  size="icon"
                  variant="ghost"
                  onClick={() => setOpen(false)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <Navigation onNavigate={() => setOpen(false)} />
            </div>
          </div>
        ) : null}
        <main className="min-w-0 p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  return (
    <nav className="grid gap-1 p-3">
      {navItems.map((item) => {
        const Icon = item.icon;
        const active =
          item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
        return (
          <Link
            key={item.to}
            className={cn(
              "flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-muted-foreground hover:bg-muted hover:text-foreground",
              active && "bg-muted text-foreground",
            )}
            to={item.to}
            onClick={onNavigate}
          >
            <Icon className="h-4 w-4" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function ThemeToggle() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("cloud-agents-theme", dark ? "dark" : "light");
  }, [dark]);
  return (
    <Button
      aria-label="Toggle theme"
      size="icon"
      variant="ghost"
      onClick={() => setDark((value) => !value)}
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}
