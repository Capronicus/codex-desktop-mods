// Each native section stays in its own React tree. Its portal follows the real project row.
function cscNestedGroup({React, portal, section, parents, children}) {
  const [mount, setMount] = React.useState(null);
  React.useLayoutEffect(() => {
    let registry = window.cscSubsectionRegistry;
    if (!registry) {
      const groups = new Set();
      let frame = 0, sidebar;
      function place() {
        frame = 0;
        sidebar = document.querySelector('[data-app-action-sidebar-scroll]');
        for (const group of groups) {
          let row;
          for (const id of group.parents) {
            row = document.querySelector('[data-app-action-sidebar-project-id="' + CSS.escape(id) + '"]');
            if (row) break;
          }
          const host = row?.closest('[role="listitem"][data-sidebar-project-container-id^="project:"],div[data-csc-project-group]') || row?.parentElement;
          if (host && !group.node.contains(host)) {
            if (group.node.parentElement !== host) host.append(group.node);
            const collapsed = row.getAttribute('data-app-action-sidebar-project-collapsed') === 'true';
            if (group.node.hidden !== collapsed) group.node.hidden = collapsed;
            group.show(group.node);
          } else {
            group.node.remove();
            const projectsCollapsed = document.querySelector('[data-app-action-sidebar-section-heading="Projects"][data-app-action-sidebar-section-collapsed="true"]');
            group.show(projectsCollapsed ? group.node : null);
          }
        }
      }
      const queue = () => { if (!frame) frame = requestAnimationFrame(place); };
      const observer = new MutationObserver(changes => {
        if (!sidebar?.isConnected || changes.some(change => sidebar.contains(change.target))) queue();
      });
      registry = window.cscSubsectionRegistry = {add(group) {
        if (!groups.size) observer.observe(document.documentElement, {subtree:true,childList:true,attributes:true,
          attributeFilter:['data-app-action-sidebar-project-collapsed','data-app-action-sidebar-section-collapsed','data-app-action-sidebar-project-id']});
        groups.add(group); place();
        return () => {
          groups.delete(group);group.node.remove();
          if (!groups.size) {observer.disconnect();cancelAnimationFrame(frame);frame=0;}
        };
      }};
    }
    const node = document.createElement('div');
    node.dataset.cscSubsection = section;
    node.style.marginInlineStart = '12px';
    return registry.add({parents,node,show:setMount});
  }, [section, parents]);
  return mount ? portal(children,mount) : children;
}
