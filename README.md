# A3-PS023-TC505
# CleanSlot SafeCore

**A Minimal Two-Node Cloud Hypervisor VM Controller**

**Team Name:** NEXORA  
**Problem Statement:** PS-023 – Minimal Two-Node Cloud Hypervisor VM Controller  
**Hackathon:** AIORI Hackathon 3

## About the Project

CleanSlot SafeCore is our proposed solution for managing virtual machines across two Linux hosts using Cloud Hypervisor and KVM.

When a VM creation request fails because of insufficient memory or another issue, it can be difficult to understand what went wrong. Failed operations may also leave behind processes or outdated VM records.

Our aim is to build a simple controller that checks available resources before placing a VM, explains failures clearly, and helps recover from failed operations without affecting other running VMs.

## What We Plan to Build

The system will have one central controller and two worker agents. The controller will receive requests, choose a suitable host, and communicate with the corresponding worker to manage the VM.

The main features we plan to implement are:

- Create, start, stop, and list virtual machines.
- Check available memory on both hosts before placing a VM.
- Enforce resource limits for VM requests.
- Use authenticated communication between the controller and workers.
- Show useful error messages when an operation fails.
- Clean up failed VM operations without affecting unrelated running VMs.
- Check VM states again after a controller restart.
- Record recovery results for later verification.

## How It Works

1. The user sends a request to the central controller.
2. The controller validates the request and checks the resources available on both hosts.
3. It selects a suitable host based on memory availability and configured limits.
4. The worker on that host carries out the requested VM operation through Cloud Hypervisor.
5. The controller checks whether the operation succeeded.
6. If something fails, the system identifies the problem and supports authorised cleanup.
7. After a restart, the controller checks the workers to make sure its stored VM records match the actual states.

## System Architecture

```text
             User / Dashboard
                    |
                    v
            Central Controller
           /        |         \
       API      Placement    Recovery
       Auth      Logic       & Logging
                    |
             +------+------+
             |             |
             v             v
        Worker 1       Worker 2
             |             |
             v             v
     Cloud Hypervisor  Cloud Hypervisor
             |             |
          Linux/KVM     Linux/KVM
           Host 1        Host 2
```

## Technologies

- **Cloud Hypervisor** – for managing virtual machines.
- **Linux KVM** – for hardware-assisted virtualisation.
- **Python** – proposed language for the controller and worker agents.
- **REST APIs** – for communication between components.
- **Bearer-token authentication** – for protecting API access.
- **SQLite or structured records** – for storing VM states and operation logs, depending on the final implementation.

## Setup Requirements

The system is intended to run on two Linux hosts with KVM support.

Requirements include:

- Two hosts with `/dev/kvm` available.
- Cloud Hypervisor installed on both hosts.
- Network connectivity between the controller and workers.
- A prepared VM image.
- Configured API authentication and VM resource limits.

The installation and execution commands will be added once the application structure is ready.

## Testing

We plan to test the controller using different VM management scenarios, including:

- Creating and starting VMs on both hosts.
- Handling requests when available memory is insufficient.
- Testing failed VM operations and cleanup.
- Checking that unrelated running VMs remain unaffected.
- Restarting the controller and checking whether VM records are updated correctly.

We will evaluate the results using placement correctness, cleanup correctness, recovery time, API operation latency, and state consistency.

## Team

**NEXORA**

- Saipriya Saravanan
- Sowparnika S

## References

- [Cloud Hypervisor API](https://github.com/cloud-hypervisor/cloud-hypervisor/blob/main/docs/api.md)
- [Cloud Hypervisor GitHub Repository](https://github.com/cloud-hypervisor/cloud-hypervisor)
- [RFC 6750 – Bearer Token Usage](https://www.rfc-editor.org/rfc/rfc6750)
- [RFC 9457 – HTTP API Error Responses](https://www.rfc-editor.org/rfc/rfc9457)

---

*This project is being developed as part of AIORI Hackathon 3. The implementation and testing results will be updated as development progresses.*
