import React from 'react'
import { Routes, Route, Link } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import DatabaseList from './pages/DatabaseList'
import DatabaseDetail from './pages/DatabaseDetail'
import AlertList from './pages/AlertList'

function App() {
  return (
    <div className="app">
      <nav className="navbar">
        <div className="navbar-brand">
          <Link to="/">DB Monitor</Link>
        </div>
        <ul className="navbar-menu">
          <li><Link to="/">Dashboard</Link></li>
          <li><Link to="/databases">Databases</Link></li>
          <li><Link to="/alerts">Alerts</Link></li>
        </ul>
      </nav>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/databases" element={<DatabaseList />} />
          <Route path="/databases/:id" element={<DatabaseDetail />} />
          <Route path="/alerts" element={<AlertList />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
