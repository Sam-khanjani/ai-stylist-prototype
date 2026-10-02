export const metadata = { title: "Admin login · Stylist", robots: { index: false } };

export default async function LoginPage({ searchParams }: PageProps<"/admin/login">) {
  const { error } = await searchParams;
  return (
    <main className="flex flex-1 items-center justify-center px-4">
      <form method="post" action="/api/admin/login" className="w-full max-w-xs space-y-4">
        <h1 className="text-lg font-medium tracking-[0.2em] uppercase">Stylist admin</h1>
        <input
          type="password"
          name="password"
          placeholder="Password"
          autoFocus
          required
          className="w-full rounded-md border border-border px-3 py-2 text-sm outline-none focus:border-gray-600"
        />
        {!process.env.ADMIN_PASSWORD ? (
          <p className="text-xs text-red-700">
            Admin password is not configured on this server (ADMIN_PASSWORD). Locally: add it to web/.env.local and restart
            npm run dev.
          </p>
        ) : (
          error && <p className="text-xs text-red-700">Wrong password.</p>
        )}
        <button className="w-full rounded-md bg-gray-800 py-2 text-sm font-medium text-white hover:bg-gray-900">
          Sign in
        </button>
      </form>
    </main>
  );
}
