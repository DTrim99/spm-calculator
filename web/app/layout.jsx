import { PolicyEngineShell } from "@policyengine/ui-kit/layout";
import "@policyengine/ui-kit/styles.css";

import Script from "next/script";
import "./globals.css";

import ToolHeader from "@/src/components/ToolHeader";

const GA_ID = "G-2YHG89FY0N";
const TOOL_NAME = "spm-calculator";
const SITE_URL = "https://policyengine.org/us/spm-calculator";
const DESCRIPTION =
  "Calculate Supplemental Poverty Measure (SPM) thresholds by family composition and housing tenure for SPM estimation areas: MSAs, residual metro groups and state nonmetro groups, using published and modeled inputs through 2036.";

export const metadata = {
  title: "SPM Threshold Calculator | PolicyEngine",
  description: DESCRIPTION,
  icons: { icon: "/favicon.svg" },
  alternates: {
    canonical: SITE_URL,
  },
  openGraph: {
    title: "SPM Threshold Calculator | PolicyEngine",
    description: DESCRIPTION,
    url: SITE_URL,
    siteName: "PolicyEngine",
    type: "website",
    locale: "en_US",
  },
  twitter: {
    card: "summary",
    title: "SPM Threshold Calculator | PolicyEngine",
    description: DESCRIPTION,
    site: "@ThePolicyEngine",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "WebApplication",
              name: "SPM Threshold Calculator",
              url: SITE_URL,
              description: DESCRIPTION,
              applicationCategory: "FinanceApplication",
              operatingSystem: "All",
              offers: {
                "@type": "Offer",
                price: "0",
                priceCurrency: "USD",
              },
              author: {
                "@type": "Organization",
                name: "PolicyEngine",
                url: "https://policyengine.org",
              },
            }),
          }}
        />
        <Script
          src={`https://www.googletagmanager.com/gtag/js?id=${GA_ID}`}
          strategy="afterInteractive"
        />
        <Script id="gtag-init" strategy="afterInteractive">
          {`
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            gtag('js', new Date());
            gtag('config', '${GA_ID}', { tool_name: '${TOOL_NAME}' });
          `}
        </Script>
        <Script id="engagement-tracking" strategy="afterInteractive">
          {`
            (function() {
              var TOOL_NAME = '${TOOL_NAME}';
              if (typeof window === 'undefined' || !window.gtag) return;

              var scrollFired = {};
              window.addEventListener('scroll', function() {
                var docHeight = document.documentElement.scrollHeight - window.innerHeight;
                if (docHeight <= 0) return;
                var pct = Math.floor((window.scrollY / docHeight) * 100);
                [25, 50, 75, 100].forEach(function(m) {
                  if (pct >= m && !scrollFired[m]) {
                    scrollFired[m] = true;
                    window.gtag('event', 'scroll_depth', { percent: m, tool_name: TOOL_NAME });
                  }
                });
              }, { passive: true });

              [30, 60, 120, 300].forEach(function(sec) {
                setTimeout(function() {
                  if (document.visibilityState !== 'hidden') {
                    window.gtag('event', 'time_on_tool', { seconds: sec, tool_name: TOOL_NAME });
                  }
                }, sec * 1000);
              });

              document.addEventListener('click', function(e) {
                var link = e.target && e.target.closest ? e.target.closest('a') : null;
                if (!link || !link.href) return;
                try {
                  var url = new URL(link.href, window.location.origin);
                  if (url.hostname && url.hostname !== window.location.hostname) {
                    window.gtag('event', 'outbound_click', {
                      url: link.href,
                      target_hostname: url.hostname,
                      tool_name: TOOL_NAME
                    });
                  }
                } catch (err) {}
              });
            })();
          `}
        </Script>
      </head>
      <body>
        <PolicyEngineShell country="us">
          <ToolHeader />
          {children}
        </PolicyEngineShell>
      </body>
    </html>
  );
}
