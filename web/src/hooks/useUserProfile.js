import { createContext, useContext, useEffect, useState } from 'react'
import { apiGet } from '../api'

const ProfileContext = createContext({ profile: null, isLoading: true })

export function ProfileProvider({ children }) {
  const [profile, setProfile] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    apiGet('/api/profile')
      .then((data) => setProfile(data))
      .catch(() => setProfile(null))
      .finally(() => setIsLoading(false))
  }, [])

  return (
    <ProfileContext.Provider value={{ profile, isLoading }}>
      {children}
    </ProfileContext.Provider>
  )
}

export function useUserProfile() {
  return useContext(ProfileContext)
}
