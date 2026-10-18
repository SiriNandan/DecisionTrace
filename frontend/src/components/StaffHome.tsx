import React from "react";
import { Link, Page } from "../lib/router";
import { ROLE_LABEL } from "../pages/SignInPage";
import type { Me } from "../types";

interface Task { page: Page; title: string; text: string; needs: string[] }

const TASKS: Task[] = [
  { page: "connect", title: "Load decision data", text: "Choose a file, check how it was read, and load it.", needs: ["ingest:write"] },
  { page: "connect", title: "Check how a file is read", text: "Review a data layout your data officer proposed and approve it.", needs: ["mappings:approve"] },
  { page: "team", title: "Manage your team", text: "Add people, choose what each can do, and withdraw keys.", needs: ["team:manage"] },
  { page: "signals", title: "Risk signals", text: "Patterns for staff to look at, never conclusions.", needs: ["signals:read"] },
  { page: "quality", title: "Data checks", text: "Values that look missing, unlikely or inconsistent.", needs: ["quality:read"] },
  { page: "seals", title: "Record seals", text: "Check that saved decisions haven’t been changed.", needs: ["seals:read"] },
];

const WHY: Record<string, string> = {
  lender_ingest: "You load decision data. You can’t read the decisions themselves, which keeps loading and reviewing separate.",
  lender_admin: "You look after who has access, and you can read decisions and insights. You can’t change a decision.",
};

/** What this person can do, for a sign-in that has no decision list to land on. */
export const StaffHome: React.FC<{ me: Me }> = ({ me }) => {
  const mine = TASKS.filter((t) => t.needs.every((n) => me.scopes.includes(n)));
  return (
    <div className="cl-page" data-testid="staff-home">
      <p className="cl-eyebrow">{me.tenant_name}</p>
      <h1 className="cl-page-title" tabIndex={-1}>Welcome, {ROLE_LABEL[me.role] ?? me.role}</h1>
      <p className="cl-page-lede">{WHY[me.role] ?? "Here is what this sign-in can do."}</p>
      {mine.length === 0 ? (
        <p className="cl-muted">This sign-in has nothing to do here yet. Ask your team administrator to check your role.</p>
      ) : (
        <ul className="cl-findings">
          {mine.map((t) => (
            <li key={t.title} className="cl-finding is-info">
              <p className="cl-finding-msg"><Link to={{ page: t.page }}><strong>{t.title}</strong></Link></p>
              <p className="cl-muted">{t.text}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default StaffHome;
