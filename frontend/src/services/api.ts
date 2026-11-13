export type LabStatus = 'active' | 'planned' | string

export interface Lab {
  id: number
  name: string
  status: LabStatus
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export async function getLabs(): Promise<Lab[]> {
  const response = await fetch(`${apiBaseUrl}/api/labs`)
  if (!response.ok) {
    throw new Error(`Backend trả về lỗi HTTP ${response.status}.`)
  }

  const data: unknown = await response.json()
  if (!Array.isArray(data)) {
    throw new Error('Dữ liệu danh sách Lab từ backend không hợp lệ.')
  }

  return data.map((item: unknown) => {
    if (
      typeof item !== 'object' ||
      item === null ||
      !('id' in item) ||
      typeof item.id !== 'number' ||
      !('name' in item) ||
      typeof item.name !== 'string' ||
      !('status' in item) ||
      typeof item.status !== 'string'
    ) {
      throw new Error('Backend trả về một Lab không đúng định dạng.')
    }

    return { id: item.id, name: item.name, status: item.status }
  })
}