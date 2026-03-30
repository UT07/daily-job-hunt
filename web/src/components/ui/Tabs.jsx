export default function Tabs({ tabs, activeTab, onTabChange }) {
  return (
    <div className="flex border-b-2 border-black overflow-x-auto">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onTabChange(tab.id)}
          className={`px-5 py-3 text-sm font-heading font-bold whitespace-nowrap transition-colors cursor-pointer
            ${
              activeTab === tab.id
                ? 'bg-yellow text-black border-b-2 border-yellow -mb-[2px]'
                : 'text-stone-500 hover:text-black hover:bg-stone-100'
            }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
