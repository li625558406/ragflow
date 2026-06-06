export default function CcgpSearch() {
  return (
    <div className="h-full bg-white overflow-hidden">
      <iframe
        src="http://www.ccgp.gov.cn/search/cr/"
        className="w-full h-full border-0"
        title="政府采购严重违法失信行为记录名单"
        loading="lazy"
      />
    </div>
  );
}
