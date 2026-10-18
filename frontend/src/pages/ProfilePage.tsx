import React from "react";
import { Link } from "../lib/router";
import { firstName } from "../components/SiteChrome";
import type { Me } from "../types";
import { ROLE_LABEL } from "./SignInPage";

const SCOPE_TEXT: Array<[string, string]> = [
  ["decisions:read", "Open and read decisions"], ["overrides:write", "Record reviews and overrides"], ["claims:issue", "Give customers their link"],
  ["ingest:write", "Load decision data"], ["quality:read", "See data checks"], ["signals:read", "See risk signals"],
  ["seals:read", "See record seals"], ["audit:read", "Read the access log"], ["team:manage", "Manage the team"],
];

export const ProfilePage: React.FC<{ me: Me }> = ({ me }) => {
  const name = me.label && me.label !== "DT_API_KEY" ? me.label : ROLE_LABEL[me.role] ?? me.role;
  const roles = me.roles?.length ? me.roles : [me.role];
  const can = SCOPE_TEXT.filter(([s]) => me.scopes.includes(s));
  return (
    <div className="cl-page cl-profile-page">
      <p className="cl-eyebrow">Your profile</p>
      <div className="cl-profile-head">
        <span className="cl-avatar cl-avatar-lg" aria-hidden="true">{firstName(me).charAt(0)}</span>
        <div><h1 className="cl-page-title" tabIndex={-1}>{name}</h1><p className="cl-muted">{me.email || "No email on file"}</p></div>
      </div>
      <dl className="cl-facts">
        <div><dt>Name</dt><dd>{name}</dd></div>
        <div><dt>Email</dt><dd>{me.email || "Not recorded"}</dd></div>
        <div><dt>Lender</dt><dd>{me.tenant_name}</dd></div>
        <div><dt>{roles.length > 1 ? "Roles" : "Role"}</dt><dd>{roles.map((r) => ROLE_LABEL[r] ?? r).join(", ")}</dd></div>
        <div><dt>Key expires</dt><dd>{me.expires_at ? new Date(me.expires_at).toLocaleDateString("en-GB") : "Never"}</dd></div>
      </dl>
      <section className="cl-section" aria-labelledby="pf-can">
        <h2 id="pf-can" className="cl-h2">What you can do</h2>
        <ul className="cl-can">{can.map(([, t]) => <li key={t}>{t}</li>)}</ul>
      </section>
      {me.scopes.includes("team:manage") && <p><Link to={{ page: "team" }} className="cl-btn cl-btn-ghost">Manage your team</Link></p>}
    </div>
  );
};

export default ProfilePage;
