"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "./auth-provider";
import { useJobs } from "./jobs-provider";
import { useState } from "react";

export function GlobalNav() {
  const { user, signOut, localMode } = useAuth();
  const { jobs } = useJobs();
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);

  if (!user) return null; // Only show for authenticated users

  const activeJobsCount = jobs.filter((j) => ["queued", "running", "in_progress", "pending"].includes(j.status)).length;

  return (
    <>
      <header className="app-toolbar">
        <Link className="app-brand" href="/"><span>TR</span><strong>TrendRelay</strong></Link>
        <nav className="app-nav">
          <Link className={pathname === "/" ? "active" : ""} href="/">Pipeline</Link>
          <Link className={pathname === "/research" ? "active" : ""} href="/research">Research</Link>
          <Link className={pathname === "/opportunities" ? "active" : ""} href="/opportunities">Opportunities</Link>
          <Link className={pathname === "/library" ? "active" : ""} href="/library">Library</Link>
          <Link className={pathname === "/studio" ? "active" : ""} href="/studio">Studio</Link>
          <Link className={pathname === "/campaigns" ? "active" : ""} href="/campaigns">Campaigns</Link>
          <Link className={pathname === "/publish" ? "active" : ""} href="/publish">Publish</Link>
          <Link className={pathname === "/tools" ? "active" : ""} href="/tools">Tools</Link>
        </nav>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <button className="text-button" onClick={() => setDrawerOpen(!drawerOpen)} style={{ position: 'relative' }}>
            Jobs
            {activeJobsCount > 0 && <span style={{ position: 'absolute', top: '-4px', right: '-4px', background: 'var(--link)', color: '#fff', borderRadius: '50%', width: '16px', height: '16px', fontSize: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold' }}>{activeJobsCount}</span>}
          </button>
          {localMode ? <span className="local-admin-badge" title="Development-only loopback session">Local admin</span> : <button className="text-button" onClick={() => void signOut()}>Sign out</button>}
        </div>
      </header>

      {drawerOpen && (
        <div style={{ position: 'absolute', top: '51px', right: '16px', width: '320px', maxHeight: '400px', overflowY: 'auto', background: 'var(--panel)', border: '1px solid var(--line)', borderTop: 'none', borderRadius: '0 0 6px 6px', boxShadow: '0 4px 12px rgba(0,0,0,0.1)', zIndex: 99 }}>
          <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--line-strong)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 500 }}>Recent Jobs</h3>
            <button className="icon-button" style={{ width: '24px', height: '24px' }} onClick={() => setDrawerOpen(false)}>×</button>
          </div>
          {jobs.length === 0 ? (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--muted)', fontSize: '12px' }}>No recent jobs</div>
          ) : (
            <div style={{ display: 'grid' }}>
              {jobs.slice(0, 15).map(job => (
                <div key={job.id} style={{ padding: '12px 16px', borderBottom: '1px solid var(--line-strong)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                    <span style={{ fontSize: '11px', textTransform: 'uppercase', color: 'var(--muted)', fontWeight: 600 }}>{job.category}</span>
                    <span style={{ fontSize: '11px', padding: '2px 6px', borderRadius: '4px', background: job.status === 'succeeded' ? '#e6f6ee' : job.status === 'failed' ? '#f8d7da' : '#fff8e1', color: job.status === 'succeeded' ? 'var(--green)' : job.status === 'failed' ? 'var(--red)' : '#b78103', fontWeight: 500 }}>{job.status}</span>
                  </div>
                  <strong style={{ display: 'block', fontSize: '12px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginBottom: '4px' }}>{job.title}</strong>
                  <time style={{ fontSize: '10px', color: 'var(--muted)' }}>{new Date(job.created_at).toLocaleString()}</time>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
