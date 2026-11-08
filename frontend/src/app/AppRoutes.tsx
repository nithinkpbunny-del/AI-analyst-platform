import { Navigate, Route, Routes } from 'react-router'
import { PlaceholderPage } from '../features/placeholder/PlaceholderPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { AppShell } from './AppShell'
import { adminNav, mainNav } from './nav'

export function AppRoutes() {
  const placeholders = [...mainNav, ...adminNav].filter((item) => item.key !== 'settings')
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/today" replace />} />
        {placeholders.map((item) => (
          <Route key={item.key} path={item.path} element={<PlaceholderPage item={item} />} />
        ))}
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/today" replace />} />
      </Route>
    </Routes>
  )
}
