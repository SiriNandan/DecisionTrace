import React from "react";
import { Link } from "../lib/router";

export const NotFoundPage: React.FC = () => (
  <div className="cl-empty" role="status">
    <div className="cl-empty-art" aria-hidden="true">404</div>
    <h1 className="cl-h2" tabIndex={-1}>We can’t find that page</h1>
    <p>The link may be out of date or mistyped.</p>
    <div className="cl-cta">
      <Link className="cl-btn cl-btn-primary" to={{ page: "home" }}>Go to decisions</Link>
      <Link className="cl-btn cl-btn-ghost" to={{ page: "help" }}>Get help</Link>
    </div>
  </div>
);

export default NotFoundPage;
